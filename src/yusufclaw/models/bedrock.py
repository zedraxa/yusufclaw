"""AWS Bedrock model provider implementation using boto3 Converse API."""

from typing import Any

import boto3

from yusufclaw.models.types import Message, ModelProvider, ModelRequest, ModelResponse, ToolCall


class BedrockProvider(ModelProvider):
    """AWS Bedrock provider using the Converse API via boto3."""

    def __init__(
        self,
        model_id: str,
        profile: str = "yusufclaw",
        region: str = "us-east-1",
        max_tokens: int = 2048,
        client: Any | None = None,
    ) -> None:
        self.model_id = model_id
        self.profile = profile
        self.region = region
        self.max_tokens = max_tokens
        self._client = client

    def _get_client(self) -> Any:
        """Lazily initialize boto3 bedrock-runtime client."""
        if self._client is not None:
            return self._client
        session = boto3.Session(profile_name=self.profile, region_name=self.region)
        self._client = session.client("bedrock-runtime")
        return self._client

    def _format_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        aws_messages: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == "tool":
                tool_result_block = {
                    "toolResult": {
                        "toolUseId": msg.tool_call_id or "unknown_call",
                        "content": [{"text": msg.content}],
                        "status": "success",
                    }
                }
                # Bedrock requires consecutive tool results to be grouped in a single user message
                if aws_messages and aws_messages[-1]["role"] == "user":
                    last_content = aws_messages[-1]["content"]
                    if isinstance(last_content, list) and any("toolResult" in block for block in last_content):
                        last_content.append(tool_result_block)
                        continue
                aws_messages.append({"role": "user", "content": [tool_result_block]})

            elif msg.role == "user":
                aws_messages.append({"role": "user", "content": [{"text": msg.content}]})

            elif msg.role == "assistant":
                content_blocks: list[dict[str, Any]] = []
                if msg.content:
                    content_blocks.append({"text": msg.content})
                for call in msg.tool_calls:
                    content_blocks.append(
                        {
                            "toolUse": {
                                "toolUseId": call.id,
                                "name": call.name,
                                "input": call.input,
                            }
                        }
                    )
                aws_messages.append({"role": "assistant", "content": content_blocks})

        return aws_messages

    def _format_tools(self, tools: list[Any]) -> list[dict[str, Any]]:
        return [
            {
                "toolSpec": {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": {"json": t.input_schema},
                }
            }
            for t in tools
        ]

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Call AWS Bedrock Converse API."""
        if not self.model_id:
            raise ValueError("bedrock_model_id is required for BedrockProvider")

        client = self._get_client()

        params: dict[str, Any] = {
            "modelId": self.model_id,
            "messages": self._format_messages(request.messages),
            "inferenceConfig": {"maxTokens": request.max_tokens or self.max_tokens},
        }

        if request.system:
            params["system"] = [{"text": request.system}]

        if request.tools:
            params["toolConfig"] = {"tools": self._format_tools(request.tools)}

        response = client.converse(**params)

        stop_reason = response.get("stopReason")
        if stop_reason and stop_reason not in {"end_turn", "tool_use", "stop_sequence", "max_tokens"}:
            raise RuntimeError(f"Bedrock stopped unexpectedly with reason: {stop_reason}")

        output = response.get("output", {})
        message = output.get("message", {})
        content_blocks = message.get("content", [])

        texts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in content_blocks:
            if "text" in block:
                texts.append(block["text"])
            elif "toolUse" in block:
                tu = block["toolUse"]
                tool_calls.append(
                    ToolCall(
                        id=tu.get("toolUseId", ""),
                        name=tu.get("name", ""),
                        input=tu.get("input", {}),
                    )
                )

        usage = response.get("usage", {})
        usage_dict = {
            "prompt_tokens": usage.get("inputTokens", 0),
            "completion_tokens": usage.get("outputTokens", 0),
        }

        return ModelResponse(
            text="".join(texts),
            tool_calls=tool_calls,
            usage=usage_dict,
        )
