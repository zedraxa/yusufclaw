"""Ollama model provider implementation using httpx."""

import uuid
from typing import Any

import httpx

from yusufclaw.models.types import Message, ModelProvider, ModelRequest, ModelResponse, ToolCall


class OllamaProvider(ModelProvider):
    """Local Ollama model provider communicating via HTTP API."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "llama3.2",
        timeout_ms: int = 120000,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_ms / 1000.0

    def _format_messages(self, system: str, messages: list[Message]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        if system:
            formatted.append({"role": "system", "content": system})

        for msg in messages:
            item: dict[str, Any] = {"role": msg.role, "content": msg.content}
            if msg.tool_calls:
                item["tool_calls"] = [
                    {
                        "function": {
                            "name": tc.name,
                            "arguments": tc.input,
                        }
                    }
                    for tc in msg.tool_calls
                ]
            formatted.append(item)
        return formatted

    def _format_tools(self, tools: list[Any]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for t in tools:
            formatted.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
            )
        return formatted

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Call Ollama /api/chat with messages and tool definitions."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._format_messages(request.system, request.messages),
            "stream": False,
        }

        if request.tools:
            payload["tools"] = self._format_tools(request.tools)

        options: dict[str, Any] = {}
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if options:
            payload["options"] = options

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()

        message = data.get("message", {})
        raw_tool_calls = message.get("tool_calls", [])
        tool_calls: list[ToolCall] = []

        for tc in raw_tool_calls:
            fn = tc.get("function", {})
            call_id = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
            tool_calls.append(
                ToolCall(
                    id=call_id,
                    name=fn.get("name", ""),
                    input=fn.get("arguments", {}),
                )
            )

        usage = {
            "prompt_tokens": data.get("prompt_eval_count", 0),
            "completion_tokens": data.get("eval_count", 0),
        }

        return ModelResponse(
            text=message.get("content", "") or "",
            tool_calls=tool_calls,
            usage=usage,
        )
