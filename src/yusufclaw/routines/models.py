"""Data models for YusufClaw routines."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

ChannelType = Literal["cli", "telegram", "web"]


@dataclass(frozen=True)
class RoutineTarget:
    """Target destination for routine output."""

    channel: ChannelType
    user_id: str
    conversation_id: str

    @property
    def userId(self) -> str:
        """CamelCase alias for user_id."""
        return self.user_id

    @property
    def conversationId(self) -> str:
        """CamelCase alias for conversation_id."""
        return self.conversation_id

    def to_dict(self) -> dict[str, str]:
        """Serialize target into dictionary matching TypeScript layout."""
        return {
            "channel": self.channel,
            "userId": self.user_id,
            "conversationId": self.conversation_id,
        }


@dataclass
class Routine:
    """Scheduled routine definition."""

    id: str
    cron: str
    prompt: str
    target: RoutineTarget

    @property
    def dedup_key(self) -> str:
        """Return deterministic memory state key matching TypeScript layout."""
        target_json = json.dumps(self.target.to_dict(), separators=(",", ":"))
        return f"routine:{self.id}:{target_json}"


@dataclass(frozen=True)
class RoutineMessage:
    """Message payload dispatched to agent / transport."""

    channel: str
    user_id: str
    conversation_id: str
    request_id: str
    text: str

    @property
    def userId(self) -> str:
        """CamelCase alias for user_id."""
        return self.user_id

    @property
    def conversationId(self) -> str:
        """CamelCase alias for conversation_id."""
        return self.conversation_id

    @property
    def requestId(self) -> str:
        """CamelCase alias for request_id."""
        return self.request_id

    def to_target(self) -> RoutineTarget:
        """Extract RoutineTarget from message."""
        channel_val: ChannelType = "cli"
        if self.channel in ("cli", "telegram", "web"):
            channel_val = self.channel  # type: ignore[assignment]
        return RoutineTarget(
            channel=channel_val,
            user_id=self.user_id,
            conversation_id=self.conversation_id,
        )


__all__ = ["ChannelType", "Routine", "RoutineMessage", "RoutineTarget"]
