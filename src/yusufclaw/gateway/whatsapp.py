"""WhatsApp Business Cloud API gateway adapter for YusufClaw.

Implements the Meta WhatsApp Cloud API interface:
- Webhook verification handshake (hub.mode, hub.verify_token, hub.challenge).
- X-Hub-Signature-256 HMAC-SHA256 signature verification.
- Sender phone number allowlist authorization (owner only).
- Text message routing to/from MessageBus.
- Interactive reply buttons for Verifiable Action Card approvals (YC-05).
- Voice note audio download and STT transcription, optional TTS audio reply.
- Chunked text splitting within WhatsApp's 4096-character limit.
- FastAPI APIRouter endpoint factory for webhook hosting.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import re
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from yusufclaw.gateway.approval import (
    ActionCard,
    ApprovalProvider,
    format_telegram_card_message,
)
from yusufclaw.gateway.bus import IncomingMessage, MessageBus
from yusufclaw.voice.types import SpeechToText, TextToSpeech


def normalize_phone_number(number: str) -> str:
    """Normalize phone number by stripping all non-digit characters."""
    return re.sub(r"\D", "", number)


def split_whatsapp_text(text: str, max_chunk_size: int = 4000) -> list[str]:
    """Split text into chunks of at most max_chunk_size without breaking characters."""
    chunks: list[str] = []
    chunk = ""
    for char in text:
        if len(chunk) + len(char) > max_chunk_size:
            chunks.append(chunk)
            chunk = ""
        chunk += char
    if chunk:
        chunks.append(chunk)
    return chunks


# --- WhatsApp Cloud API Webhook Models ---


class WhatsAppMetadata(BaseModel):
    """Metadata in WhatsApp webhook value."""

    model_config = ConfigDict(extra="ignore")

    display_phone_number: str | None = None
    phone_number_id: str | None = None


class WhatsAppContact(BaseModel):
    """Contact object in WhatsApp webhook value."""

    model_config = ConfigDict(extra="ignore")

    wa_id: str | None = None
    profile: dict[str, Any] = Field(default_factory=dict)


class WhatsAppTextMessage(BaseModel):
    """Text container in incoming WhatsApp message."""

    model_config = ConfigDict(extra="ignore")

    body: str


class WhatsAppInteractiveReply(BaseModel):
    """Button reply details."""

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str


class WhatsAppInteractiveMessage(BaseModel):
    """Interactive response container."""

    model_config = ConfigDict(extra="ignore")

    type: str = "button_reply"
    button_reply: WhatsAppInteractiveReply | None = None


class WhatsAppMediaMessage(BaseModel):
    """Audio or voice note container."""

    model_config = ConfigDict(extra="ignore")

    id: str
    mime_type: str | None = None
    voice: bool | None = None


class WhatsAppIncomingMessage(BaseModel):
    """Single incoming WhatsApp message from user."""

    model_config = ConfigDict(extra="ignore")

    from_: str = Field(alias="from")
    id: str
    timestamp: str | None = None
    type: str = "text"
    text: WhatsAppTextMessage | None = None
    interactive: WhatsAppInteractiveMessage | None = None
    audio: WhatsAppMediaMessage | None = None
    voice: WhatsAppMediaMessage | None = None


class WhatsAppValue(BaseModel):
    """Change value containing incoming messages or statuses."""

    model_config = ConfigDict(extra="ignore")

    messaging_product: str | None = None
    metadata: WhatsAppMetadata | None = None
    contacts: list[WhatsAppContact] = Field(default_factory=list)
    messages: list[WhatsAppIncomingMessage] = Field(default_factory=list)
    statuses: list[dict[str, Any]] = Field(default_factory=list)


class WhatsAppChange(BaseModel):
    """Field change object."""

    model_config = ConfigDict(extra="ignore")

    field: str
    value: WhatsAppValue


class WhatsAppEntry(BaseModel):
    """Top-level entry object in webhook notification."""

    model_config = ConfigDict(extra="ignore")

    id: str
    changes: list[WhatsAppChange] = Field(default_factory=list)


class WhatsAppWebhookPayload(BaseModel):
    """Root WhatsApp Cloud API webhook JSON payload."""

    model_config = ConfigDict(extra="ignore")

    object: str
    entry: list[WhatsAppEntry] = Field(default_factory=list)


# --- WhatsApp Cloud API Transport Client ---


class WhatsAppTransport:
    """Protocol / base class for sending requests to Meta Graph API."""

    async def call(
        self,
        method: str,
        endpoint: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        raise NotImplementedError

    async def send_text(self, to: str, text: str) -> Any:
        raise NotImplementedError

    async def send_interactive_buttons(
        self, to: str, body_text: str, buttons: list[dict[str, str]]
    ) -> Any:
        raise NotImplementedError

    async def send_audio(self, to: str, audio_url_or_id: str) -> Any:
        raise NotImplementedError

    async def download_media(self, media_id: str) -> bytes:
        raise NotImplementedError


class WhatsAppApi(WhatsAppTransport):
    """Concrete WhatsApp Cloud API client with token masking and error handling."""

    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        *,
        api_version: str = "v20.0",
        base_url: str = "https://graph.facebook.com",
        fetcher: Any | None = None,
        timeout: float = 40.0,
    ) -> None:
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.api_version = api_version
        self.base_url = base_url.rstrip("/")
        self.fetcher = fetcher
        self.timeout = timeout

    async def call(
        self,
        method: str,
        endpoint: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Call a Graph API endpoint with token masking."""
        clean_endpoint = endpoint.lstrip("/")
        url = f"{self.base_url}/{self.api_version}/{clean_endpoint}"
        req_headers = {"Authorization": f"Bearer {self.access_token}"}
        if headers:
            req_headers.update(headers)

        if self.fetcher is not None:
            try:
                res = self.fetcher(
                    url,
                    method=method,
                    headers=req_headers,
                    json=body,
                )
                if inspect.isawaitable(res):
                    response = await res
                else:
                    response = res
            except Exception as exc:
                raise RuntimeError("WhatsApp API request failed") from exc

            status_code = getattr(response, "status_code", 200)
            if hasattr(response, "json"):
                data = response.json()
                if inspect.isawaitable(data):
                    data = await data
            else:
                data = response

            if not (200 <= status_code < 300):
                raise RuntimeError(f"WhatsApp API failed with status {status_code}")
            return data

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=req_headers,
                    json=body,
                )
        except Exception as exc:
            raise RuntimeError("WhatsApp API network request failed") from exc

        if not response.is_success:
            raise RuntimeError(f"WhatsApp API failed ({response.status_code})")

        return response.json()

    async def send_text(self, to: str, text: str) -> Any:
        """Send a standard text message to a WhatsApp recipient."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": text,
            },
        }
        return await self.call("POST", f"{self.phone_number_id}/messages", body=payload)

    async def send_interactive_buttons(
        self, to: str, body_text: str, buttons: list[dict[str, str]]
    ) -> Any:
        """Send an interactive reply buttons message (maximum 3 buttons permitted by Meta)."""
        action_buttons = [
            {
                "type": "reply",
                "reply": {
                    "id": b["id"],
                    "title": b["title"][:20],  # Meta limit: max 20 chars
                },
            }
            for b in buttons[:3]
        ]
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text},
                "action": {"buttons": action_buttons},
            },
        }
        return await self.call("POST", f"{self.phone_number_id}/messages", body=payload)

    async def send_audio(self, to: str, audio_url_or_id: str) -> Any:
        """Send an audio note to a recipient."""
        audio_payload: dict[str, Any]
        if audio_url_or_id.startswith("http://") or audio_url_or_id.startswith("https://"):
            audio_payload = {"link": audio_url_or_id}
        else:
            audio_payload = {"id": audio_url_or_id}

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "audio",
            "audio": audio_payload,
        }
        return await self.call("POST", f"{self.phone_number_id}/messages", body=payload)

    async def download_media(self, media_id: str) -> bytes:
        """Retrieve media URL by media_id and download binary audio bytes."""
        # Step 1: Query media details to get download URL
        meta_info = await self.call("GET", media_id)
        media_url = meta_info.get("url") if isinstance(meta_info, dict) else None
        if not media_url:
            raise RuntimeError(f"Could not retrieve media URL for media ID: {media_id}")

        req_headers = {"Authorization": f"Bearer {self.access_token}"}
        if self.fetcher is not None:
            res = self.fetcher(media_url, method="GET", headers=req_headers)
            if inspect.isawaitable(res):
                res = await res
            if hasattr(res, "content"):
                return res.content  # type: ignore[no-any-return]
            if isinstance(res, bytes):
                return res
            return b""

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(media_url, headers=req_headers)
            if not resp.is_success:
                raise RuntimeError(f"Failed to download media: {resp.status_code}")
            return resp.content


# --- WhatsApp Adapter ---


class WhatsAppAdapter:
    """WhatsApp Cloud API gateway adapter with verification, HMAC, and approvals."""

    def __init__(
        self,
        bus: MessageBus | None = None,
        api: WhatsAppTransport | None = None,
        allowed_numbers: set[str] | list[str] | None = None,
        *,
        verify_token: str = "",
        app_secret: str = "",
        approval_provider: ApprovalProvider | None = None,
        stt: SpeechToText | None = None,
        tts: TextToSpeech | None = None,
        reply_as_voice: bool = False,
    ) -> None:
        self.bus = bus
        self.api = api
        self.verify_token = verify_token
        self.app_secret = app_secret
        self.approval_provider = approval_provider
        self.stt = stt
        self.tts = tts
        self.reply_as_voice = reply_as_voice

        # Normalize allowed numbers
        self.allowed: set[str] = set()
        if allowed_numbers:
            for num in allowed_numbers:
                cleaned = normalize_phone_number(num)
                if cleaned:
                    self.allowed.add(cleaned)

        # In-flight approval callbacks and arguments
        self.pending_approvals: dict[str, asyncio.Future[bool]] = {}
        self.active_calls: dict[str, dict[str, Any]] = {}

    def verify_webhook(
        self,
        hub_mode: str | None,
        hub_verify_token: str | None,
        hub_challenge: str | None,
    ) -> str | None:
        """Verify Meta webhook handshake during initial setup (GET request)."""
        if hub_mode == "subscribe" and hub_verify_token == self.verify_token:
            return hub_challenge or ""
        return None

    def verify_signature(
        self,
        signature_header: str | None,
        body_bytes: bytes,
    ) -> bool:
        """Verify X-Hub-Signature-256 HMAC-SHA256 signature."""
        if not self.app_secret:
            # If no secret configured, signature verification is not enforced
            return True

        if not signature_header or not signature_header.startswith("sha256="):
            return False

        received_hash = signature_header[len("sha256=") :]
        expected_hash = hmac.new(
            self.app_secret.encode("utf-8"),
            body_bytes,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected_hash, received_hash)

    async def handle_button_reply(
        self,
        sender: str,
        message_id: str,
        reply_id: str,
        signal: Any = None,
    ) -> None:
        """Process interactive reply button click for ActionCard approvals."""
        parts = reply_id.split(":")
        if len(parts) != 3:
            if self.api:
                await self.api.send_text(sender, "Geçersiz onay isteği formatı.")
            return

        action, approval_id, nonce = parts

        if action == "approve":
            actual_args = self.active_calls.get(approval_id)
            if self.approval_provider is not None:
                result = self.approval_provider.consume(approval_id, nonce, actual_args)
                if not result.success:
                    if self.api:
                        await self.api.send_text(
                            sender, f"❌ Onay reddedildi: {result.reason}"
                        )
                    fut = self.pending_approvals.get(approval_id)
                    if fut and not fut.done():
                        fut.set_result(False)
                    return

            if self.api:
                await self.api.send_text(
                    sender, "✅ Eylem onaylandı ve çalıştırılıyor."
                )

            fut = self.pending_approvals.get(approval_id)
            if fut and not fut.done():
                fut.set_result(True)
            return

        if action == "deny":
            if self.approval_provider is not None:
                self.approval_provider.deny(approval_id, nonce)

            if self.api:
                await self.api.send_text(sender, "❌ Eylem reddedildi.")

            fut = self.pending_approvals.get(approval_id)
            if fut and not fut.done():
                fut.set_result(False)
            return

        if action == "details":
            info = "Kart bulunamadı."
            if self.approval_provider is not None:
                record = self.approval_provider.get(approval_id)
                if record:
                    effects_str = ", ".join(record.card.rendered.effects) or "yok"
                    sha_prefix = record.card.args_sha256[:16]
                    info = f"ℹ️ [Kart Ayrıntısı]\nEtkiler: {effects_str}\nArgs SHA: {sha_prefix}..."

            if self.api:
                await self.api.send_text(sender, info)
            return

    async def handle_message(
        self,
        message: WhatsAppIncomingMessage,
        signal: Any = None,
    ) -> None:
        """Process a single incoming WhatsApp message."""
        sender = normalize_phone_number(message.from_)
        if not sender or sender not in self.allowed:
            # Drop messages from unlisted or unauthorized sender numbers
            return

        # 1. Handle interactive button replies
        if message.type == "interactive" and message.interactive:
            if message.interactive.button_reply:
                await self.handle_button_reply(
                    sender=sender,
                    message_id=message.id,
                    reply_id=message.interactive.button_reply.id,
                    signal=signal,
                )
                return

        # 2. Extract text or transcribe audio
        text_content: str | None = None
        is_voice_message = False

        if message.type == "text" and message.text:
            text_content = message.text.body
        elif message.type in ("audio", "voice") and (message.audio or message.voice):
            media_obj = message.audio or message.voice
            assert media_obj is not None
            is_voice_message = True
            if self.stt is None:
                if self.api:
                    await self.api.send_text(
                        sender,
                        "Sesli mesaj özelliği yapılandırılmamış (STT kapalı).",
                    )
                return

            if self.api is None:
                return

            try:
                audio_bytes = await self.api.download_media(media_obj.id)
                transcription = await self.stt.transcribe(
                    audio_bytes, mime_type=media_obj.mime_type
                )
                text_content = transcription.text
            except Exception:
                if self.api:
                    await self.api.send_text(
                        sender, "Sesli mesaj deşifre edilemedi."
                    )
                return

        if not text_content or not text_content.strip():
            return

        if self.bus is None:
            return

        # 3. Define approval callback for human-in-the-loop action cards
        async def approve_callback(call: Any, card: Any = None) -> bool:
            action_card: ActionCard | None = None
            if isinstance(card, ActionCard):
                action_card = card
            elif isinstance(card, dict) and (
                "approvalId" in card or "approval_id" in card
            ):
                action_card = ActionCard.model_validate(card)

            if not action_card:
                return False

            input_args: dict[str, Any] = {}
            if hasattr(call, "input"):
                input_args = call.input
            elif isinstance(call, dict) and "input" in call:
                input_args = call["input"]

            if self.approval_provider is not None:
                self.approval_provider.register(action_card, input_args)

            app_id = action_card.approval_id
            self.active_calls[app_id] = input_args

            card_text = format_telegram_card_message(action_card)
            buttons = [
                {"id": f"approve:{app_id}:{action_card.nonce}", "title": "✅ Onayla"},
                {"id": f"deny:{app_id}:{action_card.nonce}", "title": "❌ Reddet"},
                {"id": f"details:{app_id}:{action_card.nonce}", "title": "ℹ️ Ayrıntı"},
            ]

            if self.api:
                await self.api.send_interactive_buttons(sender, card_text, buttons)

            loop = asyncio.get_running_loop()
            fut: asyncio.Future[bool] = loop.create_future()
            self.pending_approvals[app_id] = fut

            def cleanup() -> None:
                self.pending_approvals.pop(app_id, None)
                self.active_calls.pop(app_id, None)

            def on_timeout() -> None:
                if not fut.done():
                    cleanup()
                    fut.set_result(False)

            timer = loop.call_later(300.0, on_timeout)

            try:
                res = await fut
                return res
            finally:
                timer.cancel()
                cleanup()

        # 4. Dispatch to MessageBus
        reply: str
        try:
            reply = await self.bus.dispatch(
                IncomingMessage(
                    channel="whatsapp",
                    userId=sender,
                    conversationId=sender,
                    requestId=message.id,
                    text=text_content,
                ),
                approve=approve_callback,
                signal=signal,
            )
        except Exception:
            reply = (
                "İstek tamamlanamadı. Lütfen bağlantınızı ve ayarlarınızı kontrol edip "
                "tekrar deneyin."
            )

        if not self.api:
            return

        # 5. Send reply back (TTS audio if requested and available, else chunked text)
        if (self.reply_as_voice or is_voice_message) and self.tts is not None:
            try:
                audio_res = await self.tts.synthesize(reply)
                # In tests or local, if audio_bytes present
                send_audio_bytes_fn = getattr(self.api, "send_audio_bytes", None)
                if callable(send_audio_bytes_fn):
                    await send_audio_bytes_fn(sender, audio_res.audio_bytes)
                else:
                    for chunk in split_whatsapp_text(reply):
                        await self.api.send_text(sender, chunk)
                return
            except Exception:
                pass  # Fall back to text

        for chunk in split_whatsapp_text(reply):
            await self.api.send_text(sender, chunk)

    async def handle_webhook(
        self,
        payload: dict[str, Any] | bytes | WhatsAppWebhookPayload,
        signature_header: str | None = None,
        signal: Any = None,
    ) -> None:
        """Process incoming webhook notification with HMAC verification and message routing."""
        raw_bytes: bytes
        parsed: WhatsAppWebhookPayload

        if isinstance(payload, bytes):
            raw_bytes = payload
            if not self.verify_signature(signature_header, raw_bytes):
                raise PermissionError("Invalid X-Hub-Signature-256 HMAC signature")
            import json

            data = json.loads(raw_bytes.decode("utf-8"))
            parsed = WhatsAppWebhookPayload.model_validate(data)
        elif isinstance(payload, WhatsAppWebhookPayload):
            parsed = payload
        else:
            parsed = WhatsAppWebhookPayload.model_validate(payload)

        for entry in parsed.entry:
            for change in entry.changes:
                if change.field == "messages" and change.value.messages:
                    for msg in change.value.messages:
                        await self.handle_message(msg, signal=signal)


def create_whatsapp_router(
    adapter: WhatsAppAdapter,
    prefix: str = "/webhook/whatsapp",
) -> APIRouter:
    """Create a FastAPI APIRouter hosting WhatsApp verification and incoming webhooks."""
    router = APIRouter(prefix=prefix, tags=["whatsapp"])

    @router.get("", response_class=PlainTextResponse)
    async def verify(
        hub_mode: str | None = Query(default=None, alias="hub.mode"),
        hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
        hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    ) -> Response:
        challenge = adapter.verify_webhook(hub_mode, hub_verify_token, hub_challenge)
        if challenge is None:
            raise HTTPException(status_code=403, detail="Verification failed")
        return PlainTextResponse(content=challenge, status_code=200)

    @router.post("")
    async def receive_webhook(
        request: Request,
        x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    ) -> dict[str, str]:
        body = await request.body()
        if not adapter.verify_signature(x_hub_signature_256, body):
            raise HTTPException(status_code=403, detail="Invalid signature")

        try:
            await adapter.handle_webhook(body, signature_header=x_hub_signature_256)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {"status": "ok"}

    return router


__all__ = [
    "WhatsAppAdapter",
    "WhatsAppApi",
    "WhatsAppChange",
    "WhatsAppContact",
    "WhatsAppEntry",
    "WhatsAppIncomingMessage",
    "WhatsAppInteractiveMessage",
    "WhatsAppInteractiveReply",
    "WhatsAppMediaMessage",
    "WhatsAppMetadata",
    "WhatsAppTextMessage",
    "WhatsAppTransport",
    "WhatsAppValue",
    "WhatsAppWebhookPayload",
    "create_whatsapp_router",
    "normalize_phone_number",
    "split_whatsapp_text",
]
