"""OpenAI-compatible model provider implementation."""

import json
from typing import Any

from openai import AsyncOpenAI

from yusufclaw.models.types import Message, ModelProvider, ModelRequest, ModelResponse, ToolCall


class OpenAIProvider(ModelProvider):
    """Provider for OpenAI and OpenAI-compatible Chat Completions endpoints."""

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
        timeout_ms: int = 120000,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "not-set"
        self.model = model
        self.timeout_s = timeout_ms / 1000.0

    def _build_client(self) -> AsyncOpenAI:
        return AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout_s,
        )

    def _format_messages(self, system: str, messages: list[Message]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        if system:
            formatted.append({"role": "system", "content": system})

        for msg in messages:
            item: dict[str, Any] = {"role": msg.role, "content": msg.content}
            if msg.role == "tool" and msg.tool_call_id:
                item["tool_call_id"] = msg.tool_call_id
            if msg.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.input),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            formatted.append(item)
        return formatted

    def _format_tools(self, tools: list[Any]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Send chat completion request to OpenAI API."""
        client = self._build_client()

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._format_messages(request.system, request.messages),
        }

        if request.tools:
            kwargs["tools"] = self._format_tools(request.tools)
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature

        completion = await client.chat.completions.create(**kwargs)
        choice = completion.choices[0]
        choice_message = choice.message

        tool_calls: list[ToolCall] = []
        if choice_message.tool_calls:
            for tc in choice_message.tool_calls:
                parsed_input: dict[str, Any] = {}
                if tc.function.arguments:
                    try:
                        parsed_input = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        parsed_input = {"raw": tc.function.arguments}

                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        input=parsed_input,
                    )
                )

        usage_dict: dict[str, int] = {}
        if completion.usage:
            usage_dict = {
                "prompt_tokens": completion.usage.prompt_tokens,
                "completion_tokens": completion.usage.completion_tokens,
            }

        return ModelResponse(
            text=choice_message.content or "",
            tool_calls=tool_calls,
            usage=usage_dict,
        )
