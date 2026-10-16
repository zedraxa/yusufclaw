"""Type definitions and interfaces for YusufClaw model providers."""

from abc import ABC, abstractmethod
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


class ModelProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Execute a completion turn against the underlying model."""
        raise NotImplementedError
