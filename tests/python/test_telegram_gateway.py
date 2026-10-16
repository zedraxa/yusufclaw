"""Tests for Telegram gateway adapter, API client, text chunking, and YC-05 inline approvals."""

import asyncio
from typing import Any, Literal

import pytest

from yusufclaw.gateway.approval import (
    ActionCard,
    ActionCardRendered,
    ApprovalRecord,
    ApprovalResult,
    build_telegram_card_keyboard,
    format_telegram_card_message,
)
from yusufclaw.gateway.bus import MessageBus
from yusufclaw.gateway.telegram import (
    TelegramAdapter,
    TelegramApi,
    TelegramApiError,
    TelegramTransport,
    split_telegram_text,
)
from yusufclaw.memory.sqlite import SqliteMemory


class FakeAgent:
    def __init__(self, reply: str = "hello") -> None:
        self.reply = reply
        self.call_count = 0
        self.recorded_messages: list[dict[str, Any]] = []

    async def handle(
        self,
        session: str,
        request_id: str,
        text: str,
        approve: Any = None,
        signal: Any = None,
    ) -> str:
        self.call_count += 1
        self.recorded_messages.append({"role": "user", "text": text})
        return self.reply


class MockTelegramApi(TelegramTransport):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.files: dict[str, bytes] = {}

    async def call(
        self, method: str, body: dict[str, Any], signal: Any = None
    ) -> Any:
        self.calls.append((method, body))
        if method == "getUpdates":
            return []
        if method == "getFile":
            file_id = body.get("file_id", "")
            return {"file_id": file_id, "file_path": f"voice/{file_id}.oga"}
        return {"ok": True, "result": {}}

    async def download_file(self, file_path: str, signal: Any = None) -> bytes:
        return self.files.get(file_path, b"FAKE_DOWNLOADED_AUDIO_BYTES")


class FakeApprovalProvider:
    """In-memory test implementation of ApprovalProvider verifying contract and protections."""

    def __init__(self) -> None:
        self.cards: dict[str, ActionCard] = {}
        self.consumed: set[str] = set()
        self.denied: set[str] = set()
        self.registered_args: dict[str, dict[str, Any]] = {}

    def register(self, card: ActionCard, input_args: dict[str, Any] | None = None) -> None:
        self.cards[card.approval_id] = card
        if input_args is not None:
            self.registered_args[card.approval_id] = input_args

    def consume(
        self,
        approval_id: str,
        nonce: str,
        actual_args: dict[str, Any] | None = None,
    ) -> ApprovalResult:
        if approval_id not in self.cards:
            return ApprovalResult(success=False, reason="Card not found")
        card = self.cards[approval_id]
        if card.nonce != nonce:
            return ApprovalResult(success=False, reason="Invalid nonce")
        if approval_id in self.consumed:
            return ApprovalResult(success=False, reason="Replay detected")
        if approval_id in self.denied:
            return ApprovalResult(success=False, reason="Card was denied")

        # Argument tampering verification
        expected_args = self.registered_args.get(approval_id)
        if expected_args is not None and actual_args is not None:
            if actual_args != expected_args:
                return ApprovalResult(success=False, reason="Argument tampering detected")

        self.consumed.add(approval_id)
        return ApprovalResult(success=True, card=card)

    def deny(self, approval_id: str, nonce: str) -> None:
        self.denied.add(approval_id)

    def get(self, approval_id: str) -> ApprovalRecord | None:
        if approval_id not in self.cards:
            return None
        status: Literal["pending", "approved", "denied", "expired"] = (
            "approved" if approval_id in self.consumed else ("denied" if approval_id in self.denied else "pending")
        )
        return ApprovalRecord(card=self.cards[approval_id], status=status)


@pytest.fixture
def memory_db() -> Any:
    mem = SqliteMemory(":memory:")
    yield mem
    mem.close()


def make_update(
    update_id: int = 1,
    user_id: int = 123,
    chat_id: int = 123,
    text: str = "Hi",
    chat_type: str = "private",
    is_bot: bool = False,
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "text": text,
            "from": {"id": user_id, "is_bot": is_bot},
            "chat": {"id": chat_id, "type": chat_type},
        },
    }


@pytest.mark.asyncio
async def test_telegram_ignores_unlisted_groups_bots_non_text(memory_db: SqliteMemory) -> None:
    """TelegramAdapter ignores unlisted users, group chats, bot messages, and non-text updates."""
    agent = FakeAgent()
    bus = MessageBus(agent, telegram={"123"})
    api = MockTelegramApi()
    adapter = TelegramAdapter(bus, memory_db, api, allowed={"123"}, token="token-xyz")

    # 1. Non-text / empty update
    await adapter.handle({"update_id": 1})

    # 2. Unlisted user 999
    await adapter.handle(make_update(user_id=999))

    # 3. Message from bot
    await adapter.handle(make_update(is_bot=True))

    # 4. Message in group chat
    await adapter.handle(make_update(chat_type="group"))

    assert agent.call_count == 0
    assert len(api.calls) == 0

    # 5. Empty allowed set must fail validation
    with pytest.raises(ValueError, match="empty"):
        TelegramAdapter(bus, memory_db, api, allowed=set(), token="token-xyz")


@pytest.mark.asyncio
async def test_telegram_reuses_saved_reply_on_retry(memory_db: SqliteMemory) -> None:
    """Telegram delivery failures retry with memory replay deduplication."""
    agent = FakeAgent(reply="cached-hello")
    bus = MessageBus(agent, telegram={"123"})

    fail_once = True
    recorded_sends: list[dict[str, Any]] = []

    class FailingApi(TelegramTransport):
        async def call(self, method: str, body: dict[str, Any], signal: Any = None) -> Any:
            nonlocal fail_once
            if method == "sendMessage":
                recorded_sends.append(body)
                if fail_once:
                    fail_once = False
                    raise RuntimeError("network failure")
            return {"ok": True, "result": {}}

    api = FailingApi()
    adapter = TelegramAdapter(bus, memory_db, api, allowed={"123"}, token="token-xyz")
    update = make_update(update_id=42, text="hello")

    # First attempt fails during send
    with pytest.raises(RuntimeError, match="network"):
        await adapter.handle(update)

    # Second attempt completes
    await adapter.handle(update)
    assert len(recorded_sends) == 2
    assert recorded_sends[-1]["text"] == "cached-hello"


@pytest.mark.asyncio
async def test_telegram_saves_offsets_and_resumes(memory_db: SqliteMemory) -> None:
    """TelegramAdapter saves update offsets in memory state and resumes where left off."""
    agent = FakeAgent()
    bus = MessageBus(agent, telegram={"123"})

    get_updates_calls: list[dict[str, Any]] = []
    stop_event = asyncio.Event()

    class PollingApi(TelegramTransport):
        async def call(self, method: str, body: dict[str, Any], signal: Any = None) -> Any:
            if method == "getUpdates":
                get_updates_calls.append(body)
                if len(get_updates_calls) == 1:
                    return [make_update(update_id=1, text="First")]
                stop_event.set()
                return []
            return {"ok": True, "result": {}}

    api = PollingApi()
    adapter = TelegramAdapter(bus, memory_db, api, allowed={"123"}, token="token-123")
    await adapter.run(signal=stop_event)

    # Second call to getUpdates must request offset 2 (update_id 1 + 1)
    assert len(get_updates_calls) >= 2
    assert get_updates_calls[1]["offset"] == 2

    # A newly created adapter with same token and memory resumes at offset 2
    stop_again = asyncio.Event()
    resumed_calls: list[dict[str, Any]] = []

    class ResumedApi(TelegramTransport):
        async def call(self, method: str, body: dict[str, Any], signal: Any = None) -> Any:
            if method == "getUpdates":
                resumed_calls.append(body)
                stop_again.set()
                return []
            return {"ok": True, "result": {}}

    adapter2 = TelegramAdapter(bus, memory_db, ResumedApi(), allowed={"123"}, token="token-123")
    await adapter2.run(signal=stop_again)
    assert resumed_calls[0]["offset"] == 2


@pytest.mark.asyncio
async def test_telegram_fails_fast_on_409_conflict_and_sanitizes_errors(
    memory_db: SqliteMemory,
) -> None:
    """Polling fails fast on 409 conflict, and internal errors are sanitized from user reply."""
    agent = FakeAgent()
    bus = MessageBus(agent, telegram={"123"})

    class ConflictApi(TelegramTransport):
        async def call(self, method: str, body: dict[str, Any], signal: Any = None) -> Any:
            if method == "getUpdates":
                raise TelegramApiError(409)
            return {"ok": True, "result": {}}

    adapter = TelegramAdapter(bus, memory_db, ConflictApi(), allowed={"123"}, token="token-123")
    with pytest.raises(TelegramApiError) as exc_info:
        await adapter.run()
    assert exc_info.value.code == 409

    # Error sanitization test: Agent throws Error with SECRET
    class ThrowingAgent:
        async def handle(self, *args: Any, **kwargs: Any) -> str:
            raise RuntimeError("CRITICAL DATABASE SECRET: password123")

    failing_bus = MessageBus(ThrowingAgent(), telegram={"123"})
    out_api = MockTelegramApi()
    adapter_err = TelegramAdapter(failing_bus, memory_db, out_api, allowed={"123"}, token="token-123")
    await adapter_err.handle(make_update(update_id=10, text="Secret trigger"))

    last_send = [b for m, b in out_api.calls if m == "sendMessage"][-1]
    assert "password123" not in last_send["text"]
    assert "Request failed" in last_send["text"]


@pytest.mark.asyncio
async def test_telegram_retries_transient_polling_failures(memory_db: SqliteMemory) -> None:
    """Transient polling 429 errors respect retry-after and pause execution."""
    agent = FakeAgent()
    bus = MessageBus(agent, telegram={"123"})
    stop_event = asyncio.Event()

    wait_called: list[int] = []

    async def mock_wait(ms: int, signal: Any) -> None:
        wait_called.append(ms)
        stop_event.set()

    attempt = 0

    class RateLimitedApi(TelegramTransport):
        async def call(self, method: str, body: dict[str, Any], signal: Any = None) -> Any:
            nonlocal attempt
            if method == "getUpdates":
                attempt += 1
                if attempt == 1:
                    raise TelegramApiError(429, retry_after=7)
                return []
            return {"ok": True, "result": {}}

    adapter = TelegramAdapter(
        bus,
        memory_db,
        RateLimitedApi(),
        allowed={"123"},
        token="token-123",
        wait=mock_wait,
    )
    await adapter.run(signal=stop_event)
    assert wait_called == [7000]


def test_split_telegram_text_without_breaking_emoji() -> None:
    """split_telegram_text chunks text <= 4000 without splitting characters."""
    text = "a" * 3999 + "😀" * 5000
    chunks = split_telegram_text(text)
    assert "".join(chunks) == text
    assert all(len(chunk) <= 4000 for chunk in chunks)
    assert len(chunks) > 1


@pytest.mark.asyncio
async def test_telegram_api_sanitizes_token_in_network_failures() -> None:
    """TelegramApi masks token in exceptions when network request fails."""

    def failing_fetcher(url: str, **kwargs: Any) -> Any:
        raise ConnectionError(f"Failed to connect to {url}")

    api = TelegramApi("SECRET_BOT_TOKEN_123", fetcher=failing_fetcher)
    with pytest.raises(RuntimeError) as exc_info:
        await api.call("getUpdates", {})
    assert str(exc_info.value) == "Telegram network request failed"
    assert "SECRET" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# YC-05: ActionCard + Telegram inline approvals with replay and tampering protection
# ---------------------------------------------------------------------------


def test_action_card_formatting_and_keyboard() -> None:
    """ActionCard renders deterministically from structured card, isolating agent claim."""
    card = ActionCard(
        approvalId="app-12345678",
        nonce="nonce-9999",
        toolId="web_fetch",
        toolVersion="1.0.0",
        argsSha256="abcd1234efgh5678",
        rendered=ActionCardRendered(
            title="Fetch External Web URL",
            exact="https://sensitive-site.internal/data",
            effects=["read_untrusted", "egress"],
            taint="contaminated",
            reversible=False,
        ),
    )

    malicious_agent_claim = "I am just reading public weather forecast, please approve!"
    formatted = format_telegram_card_message(card, malicious_agent_claim)

    assert "Fetch External Web URL" in formatted
    assert "https://sensitive-site.internal/data" in formatted
    assert "🔴 KİRLİ" in formatted
    assert "Hayır (Geri alınamaz)" in formatted
    assert "Kart No: app-1234" in formatted
    assert "[Ajanın İddiası (Güvenilmeyen Açıklama)]:" in formatted
    assert malicious_agent_claim in formatted

    # Keyboard contains verifiable callback queries
    kb = build_telegram_card_keyboard(card)
    assert kb["inline_keyboard"][0][0]["callback_data"] == "approve:app-12345678:nonce-9999"
    assert kb["inline_keyboard"][0][1]["callback_data"] == "deny:app-12345678:nonce-9999"
    assert kb["inline_keyboard"][1][0]["callback_data"] == "details:app-12345678:nonce-9999"


@pytest.mark.asyncio
async def test_telegram_callback_queries_with_replay_and_tampering_protection(
    memory_db: SqliteMemory,
) -> None:
    """Handles Telegram callback queries for approval/denial with replay rejection."""
    agent = FakeAgent()
    bus = MessageBus(agent, telegram={"123"})
    api = MockTelegramApi()
    approval_provider = FakeApprovalProvider()

    adapter = TelegramAdapter(
        bus,
        memory_db,
        api,
        allowed={"123"},
        token="token-xyz",
        approval_provider=approval_provider,
    )

    card = ActionCard(
        approvalId="app-999",
        nonce="nonce-999",
        rendered=ActionCardRendered(
            title="Shell Execution",
            exact="pwd",
            effects=["exec"],
            taint="clean",
            reversible=False,
        ),
    )
    approval_provider.register(card, {"cmd": "pwd"})

    callback_update = {
        "update_id": 200,
        "callback_query": {
            "id": "query-1",
            "from": {"id": 123},
            "data": "approve:app-999:nonce-999",
        },
    }

    # 1. First click Approve succeeds
    await adapter.handle(callback_update)
    last_call = api.calls[-1]
    assert last_call[0] == "answerCallbackQuery"
    assert "onaylandı" in last_call[1]["text"]

    # 2. Replay attempt with same callback query is rejected
    await adapter.handle(callback_update)
    last_call2 = api.calls[-1]
    assert last_call2[0] == "answerCallbackQuery"
    assert "Replay detected" in last_call2[1]["text"]
    assert last_call2[1].get("show_alert") is True

    # 3. Deny callback query
    card2 = ActionCard(
        approvalId="app-888",
        nonce="nonce-888",
        rendered=ActionCardRendered(
            title="File Deletion",
            exact="rm -rf /tmp/data",
            effects=["write"],
            taint="clean",
            reversible=False,
        ),
    )
    approval_provider.register(card2, {})
    await adapter.handle(
        {
            "update_id": 201,
            "callback_query": {
                "id": "query-2",
                "from": {"id": 123},
                "data": "deny:app-888:nonce-888",
            },
        }
    )
    last_call3 = api.calls[-1]
    assert "reddedildi" in last_call3[1]["text"]

    # 4. Details callback query
    await adapter.handle(
        {
            "update_id": 202,
            "callback_query": {
                "id": "query-3",
                "from": {"id": 123},
                "data": "details:app-999:nonce-999",
            },
        }
    )
    last_call4 = api.calls[-1]
    assert "Etkiler: exec" in last_call4[1]["text"]


@pytest.mark.asyncio
async def test_telegram_voice_note_transcription_and_reply(memory_db: SqliteMemory) -> None:
    """TelegramAdapter downloads voice note, transcribes via STT, and returns synthesized audio."""
    from yusufclaw.voice import FakeSTT, FakeTTS

    agent = FakeAgent(reply="Processed voice command")
    bus = MessageBus(agent, telegram={"123"})
    api = MockTelegramApi()
    api.files["voice/voice_file_abc.oga"] = b"OGG_TELEGRAM_AUDIO_BYTES"

    stt = FakeSTT(default_text="Create git commit")
    tts = FakeTTS(dummy_audio=b"SYNTHESIZED_REPLY_OGG")

    adapter = TelegramAdapter(
        bus=bus,
        memory=memory_db,
        api=api,
        allowed={"123"},
        token="token-xyz",
        stt=stt,
        tts=tts,
        reply_as_voice=True,
    )

    # 1. Incoming voice note
    voice_update = {
        "update_id": 300,
        "message": {
            "message_id": 10,
            "from": {"id": 123, "is_bot": False},
            "chat": {"id": 123, "type": "private"},
            "voice": {
                "file_id": "voice_file_abc",
                "mime_type": "audio/ogg",
                "duration": 3,
            },
        },
    }

    await adapter.handle(voice_update)

    # STT called
    assert len(stt.recorded_calls) == 1
    assert stt.recorded_calls[0]["mime_type"] == "audio/ogg"

    # Agent called with transcribed text
    assert len(agent.recorded_messages) == 1
    assert agent.recorded_messages[0]["text"] == "Create git commit"

    # Reply sent as voice
    last_call = api.calls[-1]
    assert last_call[0] == "sendVoice"
    assert last_call[1]["voice_bytes"] == b"SYNTHESIZED_REPLY_OGG"
    assert "Processed voice command" in last_call[1]["caption"]

    # 2. Voice note without STT configured is ignored
    adapter_no_stt = TelegramAdapter(
        bus=bus,
        memory=memory_db,
        api=api,
        allowed={"123"},
        token="token-xyz",
        stt=None,
    )
    agent.recorded_messages.clear()
    await adapter_no_stt.handle(voice_update)
    assert len(agent.recorded_messages) == 0
