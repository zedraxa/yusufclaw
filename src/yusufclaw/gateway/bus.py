"""Async event bus for YusufClaw multi-channel routing.

Dispatches messages from CLI, Telegram, and Web channels to the core Agent,
enforcing per-channel user allowlists and constructing isolated session keys.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ChannelType = Literal["cli", "telegram", "web", "whatsapp"]


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
    agent_id: str | None = Field(default=None, alias="agentId")

    @property
    def userId(self) -> str:
        return self.user_id

    @property
    def conversationId(self) -> str:
        return self.conversation_id

    @property
    def requestId(self) -> str:
        return self.request_id

    @property
    def agentId(self) -> str | None:
        return self.agent_id


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
        whatsapp: set[str] | None = None,
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
        if whatsapp is not None:
            self.allowed["whatsapp"] = set(whatsapp)

    async def dispatch(
        self,
        message: IncomingMessage | dict[str, Any],
        approve: Any = None,
        signal: Any = None,
        ask_question: Any = None,
    ) -> str:
        """Authorize message, compute isolated session ID, and invoke Agent.handle."""
        if isinstance(message, dict):
            msg = IncomingMessage.model_validate(message)
        else:
            msg = message

        allowed_users = self.allowed.get(msg.channel)
        if allowed_users is None or msg.user_id not in allowed_users:
            raise UnauthorizedUserError("User is not authorized")

        conv_id = msg.conversation_id
        if msg.agent_id and not conv_id.startswith(f"{msg.agent_id}:"):
            conv_id = f"{msg.agent_id}:{conv_id}"

        session = json.dumps([msg.channel, msg.user_id, conv_id], separators=(",", ":"))
        handle_kwargs: dict[str, Any] = {
            "session": session,
            "request_id": msg.request_id,
            "text": msg.text,
            "approve": approve,
        }
        if signal is not None:
            handle_kwargs["signal"] = signal
        if ask_question is not None:
            handle_kwargs["ask_question"] = ask_question

        try:
            result = await self.agent.handle(**handle_kwargs)
        except TypeError:
            handle_kwargs.pop("ask_question", None)
            handle_kwargs.pop("signal", None)
            result = await self.agent.handle(**handle_kwargs)

        return str(result)


__all__ = ["ChannelType", "IncomingMessage", "MessageBus", "UnauthorizedUserError"]
