"""Type definitions and interfaces for YusufClaw model providers."""

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "assistant", "system", "tool"]


@dataclass
class ToolCall:
    """Represents a tool call requested by the model."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolDefinition:
    """Represents the schema and documentation for a callable tool."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class Message:
    """Unified message structure across providers."""

    role: Role
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class ModelRequest:
    """Normalized payload sent to a model provider."""

    messages: list[Message]
    system: str = ""
    tools: list[ToolDefinition] = field(default_factory=list)
    max_tokens: int | None = None
    temperature: float | None = None


@dataclass
class ModelResponse:
    """Normalized response returned from a model provider."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class ToolCallDelta:
    """Incremental delta for a streamed tool call."""

    index: int = 0
    id: str | None = None
    name: str | None = None
    args_delta: str | None = None
    input: dict[str, Any] | None = None


@dataclass
class StreamChunk:
    """Normalized chunk yielded during provider streaming."""

    text: str = ""
    tool_call_delta: ToolCallDelta | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    response: ModelResponse | None = None


class StreamAccumulator:
    """Accumulates text, tool calls, and usage deltas into a final ModelResponse."""

    def __init__(self) -> None:
        self.text_parts: list[str] = []
        self.tool_calls_by_index: dict[int, dict[str, Any]] = {}
        self.direct_tool_calls: list[ToolCall] = []
        self.usage: dict[str, int] = {}
        self.response: ModelResponse | None = None

    def add(
        self, chunk: StreamChunk | str | ModelResponse
    ) -> tuple[str, ToolCallDelta | None, list[ToolCall]]:
        """Add a chunk, returning (text_delta, tool_call_delta, new_tool_calls)."""
        text_delta = ""
        tool_call_delta: ToolCallDelta | None = None
        new_tool_calls: list[ToolCall] = []

        if isinstance(chunk, ModelResponse):
            self.response = chunk
            if chunk.text and not self.text_parts:
                text_delta = chunk.text
                self.text_parts.append(chunk.text)
            if chunk.tool_calls:
                new_tool_calls.extend(chunk.tool_calls)
                self.direct_tool_calls.extend(chunk.tool_calls)
            if chunk.usage:
                self.usage.update(chunk.usage)
            return text_delta, tool_call_delta, new_tool_calls

        if isinstance(chunk, str):
            if chunk:
                text_delta = chunk
                self.text_parts.append(chunk)
            return text_delta, tool_call_delta, new_tool_calls

        if chunk.response is not None:
            self.response = chunk.response

        if chunk.text:
            text_delta = chunk.text
            self.text_parts.append(chunk.text)

        if chunk.usage:
            self.usage.update(chunk.usage)

        if chunk.tool_calls:
            new_tool_calls.extend(chunk.tool_calls)
            self.direct_tool_calls.extend(chunk.tool_calls)

        if chunk.tool_call_delta is not None:
            tool_call_delta = chunk.tool_call_delta
            idx = tool_call_delta.index
            entry = self.tool_calls_by_index.setdefault(
                idx,
                {"id": "", "name": "", "args_delta": [], "input": None},
            )
            if tool_call_delta.id:
                entry["id"] = tool_call_delta.id
            if tool_call_delta.name:
                entry["name"] = tool_call_delta.name
            if tool_call_delta.args_delta:
                entry["args_delta"].append(tool_call_delta.args_delta)
            if tool_call_delta.input is not None:
                entry["input"] = tool_call_delta.input

        return text_delta, tool_call_delta, new_tool_calls

    def finalize(self) -> ModelResponse:
        """Construct or return the final aggregated ModelResponse."""
        if self.response is not None:
            final_text = self.response.text or "".join(self.text_parts)
            final_tools = (
                list(self.response.tool_calls)
                if self.response.tool_calls
                else self._build_tool_calls()
            )
            final_usage = (
                dict(self.response.usage)
                if self.response.usage
                else dict(self.usage)
            )
            return ModelResponse(
                text=final_text,
                tool_calls=final_tools,
                usage=final_usage,
            )

        return ModelResponse(
            text="".join(self.text_parts),
            tool_calls=self._build_tool_calls(),
            usage=dict(self.usage),
        )

    def _build_tool_calls(self) -> list[ToolCall]:
        calls: list[ToolCall] = list(self.direct_tool_calls)
        for idx in sorted(self.tool_calls_by_index.keys()):
            entry = self.tool_calls_by_index[idx]
            call_id = entry["id"] or f"call_{idx}"
            name = entry["name"] or ""
            parsed_input = entry["input"]
            if parsed_input is None:
                args_str = "".join(entry["args_delta"])
                if args_str:
                    try:
                        parsed_input = json.loads(args_str)
                    except json.JSONDecodeError:
                        parsed_input = {"raw": args_str}
                else:
                    parsed_input = {}
            calls.append(
                ToolCall(
                    id=call_id,
                    name=name,
                    input=parsed_input,
                )
            )
        return calls


class ModelProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Execute a completion turn against the underlying model."""
        raise NotImplementedError

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        """Execute a streaming completion turn yielding stream chunks.

        Default implementation raises NotImplementedError.
        """
        if False:
            yield StreamChunk()
        raise NotImplementedError("Streaming is not implemented for this provider")
