"""Tests for async MessageBus routing and per-channel authorization."""

import json
from typing import Any

import pytest

from yusufclaw.gateway.bus import IncomingMessage, MessageBus, UnauthorizedUserError


class FakeAgent:
    """Fake agent recording handle() invocations."""

    def __init__(self, reply: str = "hello") -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def handle(
        self,
        session: str,
        request_id: str,
        text: str,
        approve: Any = None,
        signal: Any = None,
    ) -> str:
        self.calls.append(
            {
                "session": session,
                "request_id": request_id,
                "text": text,
                "approve": approve,
                "signal": signal,
            }
        )
        return self.reply


@pytest.mark.asyncio
async def test_bus_denies_unlisted_senders() -> None:
    """MessageBus raises UnauthorizedUserError if sender is not in the channel allowlist."""
    agent = FakeAgent()
    bus = MessageBus(
        agent,
        cli={"local"},
        telegram={"123"},
        web={"web-user"},
    )

    # 1. Unlisted telegram user -> error
    with pytest.raises((UnauthorizedUserError, PermissionError)) as exc_info:
        await bus.dispatch(
            IncomingMessage(
                channel="telegram",
                userId="999",
                conversationId="1",
                requestId="req-1",
                text="hi",
            )
        )
    assert "authorized" in str(exc_info.value).lower()
    assert len(agent.calls) == 0

    # 2. Unlisted channel or missing user
    with pytest.raises((UnauthorizedUserError, PermissionError)):
        await bus.dispatch(
            {
                "channel": "cli",
                "userId": "other-user",
                "conversationId": "1",
                "requestId": "req-2",
                "text": "hi",
            }
        )
    assert len(agent.calls) == 0


@pytest.mark.asyncio
async def test_bus_session_formatting_and_isolation() -> None:
    """MessageBus formats session key deterministically as JSON array [channel, userId, conversationId]."""
    agent = FakeAgent()
    bus = MessageBus(
        agent,
        cli={"local"},
        telegram={"123"},
        web={"web-user"},
    )

    # CLI dispatch
    await bus.dispatch(
        IncomingMessage(
            channel="cli",
            userId="local",
            conversationId="work",
            requestId="r1",
            text="Hello CLI",
        )
    )
    assert len(agent.calls) == 1
    call1 = agent.calls[0]
    assert call1["session"] == json.dumps(["cli", "local", "work"], separators=(",", ":"))
    assert call1["request_id"] == "r1"
    assert call1["text"] == "Hello CLI"

    # Telegram dispatch
    await bus.dispatch(
        IncomingMessage(
            channel="telegram",
            userId="123",
            conversationId="chat-55",
            requestId="r2",
            text="Hello TG",
        )
    )
    assert len(agent.calls) == 2
    call2 = agent.calls[1]
    assert call2["session"] == json.dumps(["telegram", "123", "chat-55"], separators=(",", ":"))
    assert call2["request_id"] == "r2"
    assert call2["text"] == "Hello TG"
