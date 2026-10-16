"""Deterministic fake model provider for testing and offline execution."""

from collections.abc import AsyncIterator, Sequence

from yusufclaw.models.types import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    StreamChunk,
    ToolCall,
)


class FakeProvider(ModelProvider):
    """Fake model provider with configurable responses and request recording."""

    provider_name: str = "fake"
    model: str = "fake"

    def __init__(
        self,
        canned_responses: Sequence[ModelResponse] | None = None,
        default_text: str = "Fake model response from YusufClaw.",
        streaming: bool = False,
        canned_streams: Sequence[Sequence[StreamChunk | str | ModelResponse]] | None = None,
    ) -> None:
        self._canned_responses = list(canned_responses) if canned_responses else []
        self._default_text = default_text
        self.recorded_requests: list[ModelRequest] = []
        self._streaming = streaming
        self._canned_streams: list[list[StreamChunk | str | ModelResponse]] = (
            [list(s) for s in canned_streams] if canned_streams else []
        )

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

    def add_canned_stream(self, chunks: Sequence[StreamChunk | str | ModelResponse]) -> None:
        """Enqueue an additional canned stream."""
        self._canned_streams.append(list(chunks))

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        """Stream chunks if configured, otherwise raise NotImplementedError."""
        if not self._streaming and not self._canned_streams:
            if False:
                yield StreamChunk()
            raise NotImplementedError("Streaming not enabled on FakeProvider")

        self.recorded_requests.append(request)

        if self._canned_streams:
            chunks = self._canned_streams.pop(0)
            for c in chunks:
                if isinstance(c, StreamChunk):
                    yield c
                elif isinstance(c, ModelResponse):
                    yield StreamChunk(response=c)
                else:
                    yield StreamChunk(text=str(c))
            return

        resp = (
            self._canned_responses.pop(0)
            if self._canned_responses
            else ModelResponse(
                text=self._default_text,
                tool_calls=[],
                usage={"prompt_tokens": 10, "completion_tokens": 10},
            )
        )

        if resp.text:
            parts = resp.text.split(" ")
            for i, part in enumerate(parts):
                delta = part if i == len(parts) - 1 else part + " "
                yield StreamChunk(text=delta)

        if resp.tool_calls:
            yield StreamChunk(tool_calls=resp.tool_calls)

        if resp.usage:
            yield StreamChunk(usage=resp.usage)

        yield StreamChunk(response=resp)


class FakeStreamingProvider(FakeProvider):
    """Fake model provider that explicitly supports streaming."""

    def __init__(
        self,
        canned_responses: Sequence[ModelResponse] | None = None,
        default_text: str = "Fake model response from YusufClaw.",
        canned_streams: Sequence[Sequence[StreamChunk | str | ModelResponse]] | None = None,
    ) -> None:
        super().__init__(
            canned_responses=canned_responses,
            default_text=default_text,
            streaming=True,
            canned_streams=canned_streams,
        )
