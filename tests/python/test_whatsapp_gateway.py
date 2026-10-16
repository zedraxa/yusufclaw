"""Tests for WhatsApp Business Cloud API adapter, HMAC-SHA256, and interactive approvals."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from yusufclaw.gateway.approval import ActionCard, ActionCardRendered, InMemoryApprovalProvider
from yusufclaw.gateway.bus import MessageBus
from yusufclaw.gateway.whatsapp import (
    WhatsAppAdapter,
    WhatsAppApi,
    WhatsAppTransport,
    create_whatsapp_router,
    normalize_phone_number,
    split_whatsapp_text,
)
from yusufclaw.voice import FakeSTT, FakeTTS


class FakeAgent:
    """Fake agent recording handle() invocations."""

    def __init__(self, reply: str = "WhatsApp hello") -> None:
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
            }
        )
        if approve is not None and "require_approval" in text:
            # Trigger approval callback
            card = ActionCard(
                approvalId="app-wa-100",
                nonce="nonce-wa-100",
                rendered=ActionCardRendered(
                    title="Send Email",
                    exact="sendmail to user@example.com",
                    effects=["egress"],
                    taint="clean",
                    reversible=False,
                ),
            )
            approved = await approve({"input": {"to": "user@example.com"}}, card)
            if not approved:
                return "Operation was denied."
            return "Operation was approved and executed."

        return self.reply


class MockWhatsAppApi(WhatsAppTransport):
    """Mock transport capturing calls for offline testing."""

    def __init__(self) -> None:
        self.sent_texts: list[dict[str, Any]] = []
        self.sent_buttons: list[dict[str, Any]] = []
        self.sent_audios: list[dict[str, Any]] = []
        self.audio_bytes_store: dict[str, bytes] = {}

    async def call(
        self,
        method: str,
        endpoint: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return {"status": "ok"}

    async def send_text(self, to: str, text: str) -> Any:
        self.sent_texts.append({"to": to, "text": text})
        return {"status": "sent"}

    async def send_interactive_buttons(
        self, to: str, body_text: str, buttons: list[dict[str, str]]
    ) -> Any:
        self.sent_buttons.append({"to": to, "body_text": body_text, "buttons": buttons})
        return {"status": "sent"}

    async def send_audio(self, to: str, audio_url_or_id: str) -> Any:
        self.sent_audios.append({"to": to, "audio": audio_url_or_id})
        return {"status": "sent"}

    async def download_media(self, media_id: str) -> bytes:
        return self.audio_bytes_store.get(media_id, b"DEFAULT_MOCK_AUDIO_BYTES")


def build_whatsapp_webhook_payload(
    sender: str,
    msg_id: str = "wamid.123",
    text: str | None = "Hello YusufClaw",
    button_reply_id: str | None = None,
    button_title: str | None = None,
    audio_id: str | None = None,
) -> dict[str, Any]:
    """Helper to construct standard WhatsApp Cloud API payload."""
    message_obj: dict[str, Any] = {
        "from": sender,
        "id": msg_id,
        "timestamp": "1729080000",
    }
    if button_reply_id:
        message_obj["type"] = "interactive"
        message_obj["interactive"] = {
            "type": "button_reply",
            "button_reply": {"id": button_reply_id, "title": button_title or "Click"},
        }
    elif audio_id:
        message_obj["type"] = "audio"
        message_obj["audio"] = {"id": audio_id, "mime_type": "audio/ogg; codecs=opus"}
    else:
        message_obj["type"] = "text"
        message_obj["text"] = {"body": text or ""}

    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "BIZ_123",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550234567",
                                "phone_number_id": "PN_123",
                            },
                            "contacts": [{"wa_id": sender, "profile": {"name": "Yusuf"}}],
                            "messages": [message_obj],
                        },
                    }
                ],
            }
        ],
    }


def compute_hmac_sha256(secret: str, data: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), data, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# --- Tests ---


def test_normalize_phone_number() -> None:
    assert normalize_phone_number("+90 (555) 123-4567") == "905551234567"
    assert normalize_phone_number("1-555-0199") == "15550199"
    assert normalize_phone_number("905551234567") == "905551234567"


def test_split_whatsapp_text() -> None:
    short_text = "Hello WhatsApp"
    assert split_whatsapp_text(short_text, max_chunk_size=100) == [short_text]

    long_text = "A" * 250
    chunks = split_whatsapp_text(long_text, max_chunk_size=100)
    assert len(chunks) == 3
    assert chunks[0] == "A" * 100
    assert chunks[1] == "A" * 100
    assert chunks[2] == "A" * 50


def test_whatsapp_webhook_verification_handshake() -> None:
    adapter = WhatsAppAdapter(verify_token="my_secret_token_123")

    # Valid verification
    res = adapter.verify_webhook(
        hub_mode="subscribe",
        hub_verify_token="my_secret_token_123",
        hub_challenge="11582012",
    )
    assert res == "11582012"

    # Invalid token
    res_bad = adapter.verify_webhook(
        hub_mode="subscribe",
        hub_verify_token="wrong_token",
        hub_challenge="11582012",
    )
    assert res_bad is None

    # Invalid mode
    res_bad_mode = adapter.verify_webhook(
        hub_mode="unsubscribe",
        hub_verify_token="my_secret_token_123",
        hub_challenge="11582012",
    )
    assert res_bad_mode is None


def test_whatsapp_hmac_signature_verification() -> None:
    secret = "app_secret_xyz"
    adapter = WhatsAppAdapter(app_secret=secret)

    payload = b'{"object":"whatsapp_business_account"}'
    valid_sig = compute_hmac_sha256(secret, payload)

    assert adapter.verify_signature(valid_sig, payload) is True
    assert adapter.verify_signature("sha256=invalidhash", payload) is False
    assert adapter.verify_signature(None, payload) is False
    assert adapter.verify_signature("not_sha256_format", payload) is False


@pytest.mark.asyncio
async def test_whatsapp_allows_only_configured_owner_number() -> None:
    agent = FakeAgent(reply="Owner verified")
    bus = MessageBus(agent, whatsapp={"905551234567"})
    api = MockWhatsAppApi()
    adapter = WhatsAppAdapter(
        bus=bus,
        api=api,
        allowed_numbers={"905551234567"},
    )

    # 1. Unlisted sender: dropped, no dispatch to bus, no outbound message
    unlisted_payload = build_whatsapp_webhook_payload(
        sender="19998887766", text="Malicious stranger"
    )
    await adapter.handle_webhook(unlisted_payload)
    assert len(agent.calls) == 0
    assert len(api.sent_texts) == 0

    # 2. Allowed sender: dispatched to bus with channel='whatsapp' and reply sent back
    allowed_payload = build_whatsapp_webhook_payload(
        sender="+90 555 123 4567", text="Hello assistant"
    )
    await adapter.handle_webhook(allowed_payload)
    assert len(agent.calls) == 1
    call = agent.calls[0]
    assert json.loads(call["session"]) == ["whatsapp", "905551234567", "905551234567"]
    assert call["text"] == "Hello assistant"
    assert len(api.sent_texts) == 1
    assert api.sent_texts[0]["to"] == "905551234567"
    assert api.sent_texts[0]["text"] == "Owner verified"


@pytest.mark.asyncio
async def test_whatsapp_interactive_approval_flow_and_replay_protection() -> None:
    agent = FakeAgent()
    bus = MessageBus(agent, whatsapp={"905551234567"})
    api = MockWhatsAppApi()
    approval_provider = InMemoryApprovalProvider()
    adapter = WhatsAppAdapter(
        bus=bus,
        api=api,
        allowed_numbers={"905551234567"},
        approval_provider=approval_provider,
    )

    # 1. Send turn that triggers approval callback
    initial_payload = build_whatsapp_webhook_payload(
        sender="905551234567",
        msg_id="turn-1",
        text="Please require_approval to send email",
    )
    turn_task = asyncio.create_task(adapter.handle_webhook(initial_payload))
    await asyncio.sleep(0.05)

    # Verify interactive reply buttons sent
    assert len(api.sent_buttons) == 1
    buttons_call = api.sent_buttons[0]
    assert buttons_call["to"] == "905551234567"
    assert "Send Email" in buttons_call["body_text"]
    assert len(buttons_call["buttons"]) == 3
    assert buttons_call["buttons"][0]["id"].startswith("approve:app-wa-100:")
    assert buttons_call["buttons"][1]["id"].startswith("deny:app-wa-100:")
    assert buttons_call["buttons"][2]["id"].startswith("details:app-wa-100:")

    approve_id = buttons_call["buttons"][0]["id"]
    deny_id = buttons_call["buttons"][1]["id"]
    details_id = buttons_call["buttons"][2]["id"]

    # 2. Check details button reply
    details_payload = build_whatsapp_webhook_payload(
        sender="905551234567",
        msg_id="btn-details",
        button_reply_id=details_id,
        button_title="ℹ️ Ayrıntı",
    )
    await adapter.handle_webhook(details_payload)
    assert any("Kart Ayrıntısı" in msg["text"] for msg in api.sent_texts)

    # 3. Click Approve button reply
    approve_payload = build_whatsapp_webhook_payload(
        sender="905551234567",
        msg_id="btn-approve",
        button_reply_id=approve_id,
        button_title="✅ Onayla",
    )
    await adapter.handle_webhook(approve_payload)
    await turn_task

    # Confirmation text sent
    assert any("Eylem onaylandı ve çalıştırılıyor" in msg["text"] for msg in api.sent_texts)
    # Agent finished turn with approved outcome
    assert any("Operation was approved and executed." in msg["text"] for msg in api.sent_texts)

    # 4. Replay click on the same approve button -> rejected!
    api.sent_texts.clear()
    await adapter.handle_webhook(approve_payload)
    assert any("Replay detected" in msg["text"] for msg in api.sent_texts)

    # 5. Deny callback interaction
    deny_payload = build_whatsapp_webhook_payload(
        sender="905551234567",
        msg_id="btn-deny",
        button_reply_id=deny_id,
        button_title="❌ Reddet",
    )
    api.sent_texts.clear()
    await adapter.handle_webhook(deny_payload)
    assert any("Eylem reddedildi" in msg["text"] for msg in api.sent_texts)


@pytest.mark.asyncio
async def test_whatsapp_voice_note_transcription() -> None:
    agent = FakeAgent(reply="Understood your voice command")
    bus = MessageBus(agent, whatsapp={"905551234567"})
    api = MockWhatsAppApi()
    api.audio_bytes_store["media_audio_777"] = b"OGG_VOICE_SAMPLE_BYTES"
    stt = FakeSTT(default_text="Book meeting at 3pm")
    tts = FakeTTS()

    adapter = WhatsAppAdapter(
        bus=bus,
        api=api,
        allowed_numbers={"905551234567"},
        stt=stt,
        tts=tts,
    )

    audio_payload = build_whatsapp_webhook_payload(
        sender="905551234567",
        msg_id="voice-msg-1",
        audio_id="media_audio_777",
    )

    await adapter.handle_webhook(audio_payload)

    # STT was called with the downloaded bytes
    assert len(stt.recorded_calls) == 1
    assert stt.recorded_calls[0]["audio_len"] == len(b"OGG_VOICE_SAMPLE_BYTES")

    # Agent received the transcribed text
    assert len(agent.calls) == 1
    assert agent.calls[0]["text"] == "Book meeting at 3pm"

    # Outbound reply sent back
    assert any("Understood your voice command" in msg["text"] for msg in api.sent_texts)


def test_whatsapp_fastapi_router_endpoints() -> None:
    """Tests FastAPI router endpoints for GET verification and POST webhook."""
    secret = "fastapi_app_secret_123"
    adapter = WhatsAppAdapter(
        verify_token="test_verify_token",
        app_secret=secret,
        allowed_numbers={"905551234567"},
    )
    adapter.handle_webhook = AsyncMock()  # type: ignore[method-assign]

    app = FastAPI()
    router = create_whatsapp_router(adapter, prefix="/webhook/whatsapp")
    app.include_router(router)
    client = TestClient(app)

    # 1. GET verification: valid
    res = client.get(
        "/webhook/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "test_verify_token",
            "hub.challenge": "CHALLENGE_STRING_7788",
        },
    )
    assert res.status_code == 200
    assert res.text == "CHALLENGE_STRING_7788"

    # 2. GET verification: invalid token
    res_bad = client.get(
        "/webhook/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong_token",
            "hub.challenge": "CHALLENGE_STRING_7788",
        },
    )
    assert res_bad.status_code == 403

    # 3. POST webhook: valid HMAC signature
    body_data = b'{"object":"whatsapp_business_account","entry":[]}'
    sig = compute_hmac_sha256(secret, body_data)
    res_post = client.post(
        "/webhook/whatsapp",
        content=body_data,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    assert res_post.status_code == 200
    assert res_post.json() == {"status": "ok"}
    adapter.handle_webhook.assert_awaited_once()

    # 4. POST webhook: invalid signature
    res_post_bad = client.post(
        "/webhook/whatsapp",
        content=body_data,
        headers={"X-Hub-Signature-256": "sha256=badsignature", "Content-Type": "application/json"},
    )
    assert res_post_bad.status_code == 403


@pytest.mark.asyncio
async def test_whatsapp_api_client_with_custom_fetcher() -> None:
    recorded_calls: list[dict[str, Any]] = []

    def fake_fetcher(url: str, method: str, headers: dict[str, str], json: Any = None) -> Any:
        recorded_calls.append({"url": url, "method": method, "headers": headers, "json": json})
        if "messages" in url:
            return {"messages": [{"id": "wamid.sent_1"}]}
        if "media_123" in url:
            return {"url": "https://cdn.example.com/audio/sample.ogg"}
        if "cdn.example.com" in url:
            res = MagicMock()
            res.content = b"AUDIO_FILE_DATA_BYTES"
            return res
        return {}

    from unittest.mock import MagicMock

    api = WhatsAppApi(
        access_token="wa_secret_token_123",
        phone_number_id="PN_9999",
        fetcher=fake_fetcher,
    )

    # 1. Send text
    text_res = await api.send_text("905551234567", "Hello from API client")
    assert text_res["messages"][0]["id"] == "wamid.sent_1"
    assert "Bearer wa_secret_token_123" in recorded_calls[-1]["headers"]["Authorization"]
    assert recorded_calls[-1]["json"]["to"] == "905551234567"

    # 2. Send interactive buttons
    await api.send_interactive_buttons(
        "905551234567",
        "Action prompt",
        [{"id": "opt1", "title": "Option 1"}],
    )
    assert recorded_calls[-1]["json"]["type"] == "interactive"

    # 3. Download media
    data = await api.download_media("media_123")
    assert data == b"AUDIO_FILE_DATA_BYTES"
