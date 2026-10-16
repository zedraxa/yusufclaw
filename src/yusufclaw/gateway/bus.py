"""Async event bus for YusufClaw multi-channel routing.

Dispatches messages from CLI, Telegram, and Web channels to the core Agent,
enforcing per-channel user allowlists and constructing isolated session keys.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ChannelType = Literal["cli", "telegram", "web"]


class UnauthorizedUserError(PermissionError):
    """Raised when an unlisted or unauthorized user attempts to dispatch a message."""

    def __init__(self, message: str = "User is not authorized") -> None:
        super().__init__(message)


class IncomingMessage(BaseModel):
    """Incoming user message payload from any gateway channel."""

    model_config = ConfigDict(populate_by_name=True)

    channel: ChannelType
    user_id: str = Field(alias="userId")
    conversation_id: str = Field(alias="conversationId")
    request_id: str = Field(alias="requestId")
    text: str

    @property
    def userId(self) -> str:
        return self.user_id

    @property
    def conversationId(self) -> str:
        return self.conversation_id

    @property
    def requestId(self) -> str:
        return self.request_id


class MessageBus:
    """Multi-channel message bus enforcing channel allowlists and routing to Agent."""

    def __init__(
        self,
        agent: Any,
        allowed: dict[str, set[str]] | None = None,
        *,
        cli: set[str] | None = None,
        telegram: set[str] | None = None,
        web: set[str] | None = None,
    ) -> None:
        self.agent = agent
        self.allowed: dict[str, set[str]] = {}
        if allowed is not None:
            for ch, uids in allowed.items():
                self.allowed[ch] = set(uids)
        if cli is not None:
            self.allowed["cli"] = set(cli)
        if telegram is not None:
            self.allowed["telegram"] = set(telegram)
        if web is not None:
            self.allowed["web"] = set(web)

    async def dispatch(
        self,
        message: IncomingMessage | dict[str, Any],
        approve: Any = None,
        signal: Any = None,
    ) -> str:
        """Authorize message, compute isolated session ID, and invoke Agent.handle."""
        if isinstance(message, dict):
            msg = IncomingMessage.model_validate(message)
        else:
            msg = message

        allowed_users = self.allowed.get(msg.channel)
        if allowed_users is None or msg.user_id not in allowed_users:
            raise UnauthorizedUserError("User is not authorized")

        session = json.dumps([msg.channel, msg.user_id, msg.conversation_id], separators=(",", ":"))
        result = await self.agent.handle(
            session=session,
            request_id=msg.request_id,
            text=msg.text,
            approve=approve,
        )
        return str(result)


__all__ = ["ChannelType", "IncomingMessage", "MessageBus", "UnauthorizedUserError"]
