"""Deterministic fake model provider for testing and offline execution."""

from collections.abc import Sequence

from yusufclaw.models.types import ModelProvider, ModelRequest, ModelResponse, ToolCall


class FakeProvider(ModelProvider):
    """Fake model provider with configurable responses and request recording."""

    def __init__(
        self,
        canned_responses: Sequence[ModelResponse] | None = None,
        default_text: str = "Fake model response from YusufClaw.",
    ) -> None:
        self._canned_responses = list(canned_responses) if canned_responses else []
        self._default_text = default_text
        self.recorded_requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Record the request and return the next canned response or default text."""
        self.recorded_requests.append(request)

        if self._canned_responses:
            return self._canned_responses.pop(0)

        # Default behavior: if user asks something, return default text
        return ModelResponse(
            text=self._default_text,
            tool_calls=[],
            usage={"prompt_tokens": 10, "completion_tokens": 10},
        )

    def add_canned_response(self, text: str, tool_calls: list[ToolCall] | None = None) -> None:
        """Enqueue an additional canned response."""
        self._canned_responses.append(
            ModelResponse(text=text, tool_calls=tool_calls or [])
        )
