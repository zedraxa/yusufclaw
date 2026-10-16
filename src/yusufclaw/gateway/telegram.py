"""Telegram bot gateway adapter for YusufClaw.

Implements the Telegram Bot API interface:
- Webhook and polling modes for receiving Telegram updates.
- User ID authorization checking against configured allowlists.
- Session-based conversation routing to the core message bus.
- Interactive inline keyboards for action card approval requests (YC-05).
- Chunked message splitting without breaking Unicode / emoji characters.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from yusufclaw.gateway.approval import (
    ActionCard,
    ApprovalProvider,
    build_telegram_card_keyboard,
    format_telegram_card_message,
)
from yusufclaw.gateway.bus import IncomingMessage, MessageBus
from yusufclaw.voice.types import SpeechToText, TextToSpeech


class TelegramFrom(BaseModel):
    """Sender information in a Telegram update."""

    model_config = ConfigDict(extra="ignore")

    id: int
    is_bot: bool | None = None


class TelegramChat(BaseModel):
    """Chat container information in a Telegram update."""

    model_config = ConfigDict(extra="ignore")

    id: int
    type: str


class TelegramVoice(BaseModel):
    """Voice note or audio attachment metadata in a Telegram message."""

    model_config = ConfigDict(extra="ignore")

    file_id: str
    file_unique_id: str | None = None
    duration: int | None = None
    mime_type: str | None = None
    file_size: int | None = None


class TelegramMessage(BaseModel):
    """Telegram message object."""

    model_config = ConfigDict(extra="ignore")

    message_id: int | None = None
    text: str | None = None
    voice: TelegramVoice | None = None
    audio: TelegramVoice | None = None
    from_: TelegramFrom | None = Field(default=None, alias="from")
    chat: TelegramChat


class TelegramCallbackQuery(BaseModel):
    """Telegram inline keyboard callback query."""

    model_config = ConfigDict(extra="ignore")

    id: str
    from_: TelegramFrom = Field(alias="from")
    message: TelegramMessage | None = None
    data: str | None = None


class TelegramUpdate(BaseModel):
    """Root Telegram update schema."""

    model_config = ConfigDict(extra="ignore")

    update_id: int
    message: TelegramMessage | None = None
    callback_query: TelegramCallbackQuery | None = None


class TelegramApiError(Exception):
    """Raised when Telegram Bot API returns an error response."""

    def __init__(self, code: int, retry_after: int = 3) -> None:
        super().__init__(f"Telegram API failed ({code})")
        self.code = code
        self.retry_after = retry_after


class TelegramTransport:
    """Protocol / base class for sending Telegram Bot API requests."""

    async def call(
        self, method: str, body: dict[str, Any], signal: Any = None
    ) -> Any:
        """Call a Telegram API method with a JSON payload."""
        raise NotImplementedError

    async def download_file(self, file_path: str, signal: Any = None) -> bytes:
        """Download binary file from Telegram Bot API."""
        raise NotImplementedError


class TelegramApi(TelegramTransport):
    """Concrete Telegram API client with token URL masking and error handling."""

    def __init__(
        self,
        token: str,
        fetcher: Any | None = None,
        timeout: float = 40.0,
    ) -> None:
        self.token = token
        self.fetcher = fetcher
        self.timeout = timeout

    async def download_file(self, file_path: str, signal: Any = None) -> bytes:
        url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
        if self.fetcher is not None:
            try:
                res = self.fetcher(url, method="GET")
                if inspect.isawaitable(res):
                    res = await res
                if hasattr(res, "content"):
                    return res.content  # type: ignore[no-any-return]
                if isinstance(res, bytes):
                    return res
                return b""
            except Exception as exc:
                raise RuntimeError("Telegram network request failed") from exc

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url)
        except Exception as exc:
            raise RuntimeError("Telegram network request failed") from exc

        if not response.is_success:
            raise TelegramApiError(response.status_code)

        return response.content

    async def call(
        self, method: str, body: dict[str, Any], signal: Any = None
    ) -> Any:
        url = f"https://api.telegram.org/bot{self.token}/{method}"

        if self.fetcher is not None:
            # Custom fetcher used in tests
            try:
                res = self.fetcher(
                    url,
                    method="POST",
                    headers={"content-type": "application/json"},
                    json=body,
                )
                if inspect.isawaitable(res):
                    response = await res
                else:
                    response = res
            except TelegramApiError:
                raise
            except Exception as exc:
                raise RuntimeError("Telegram network request failed") from exc

            # Parse response from test fetcher (could be httpx.Response or mock)
            status_code = getattr(response, "status_code", 200)
            try:
                if hasattr(response, "json"):
                    data = response.json()
                    if inspect.isawaitable(data):
                        data = await data
                else:
                    data = response
            except Exception as exc:
                raise TelegramApiError(status_code) from exc

            if not isinstance(data, dict):
                raise TelegramApiError(status_code)

            if not (200 <= status_code < 300) or not data.get("ok", True):
                error_code = data.get("error_code") or status_code
                params = data.get("parameters") or {}
                retry_after = params.get("retry_after", 3)
                raise TelegramApiError(error_code, retry_after)
            return data.get("result")

        # Standard production HTTP client
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=body)
        except Exception as exc:
            # Never leak token-bearing URLs in exceptions
            raise RuntimeError("Telegram network request failed") from exc

        try:
            data = response.json()
        except Exception as exc:
            raise TelegramApiError(response.status_code) from exc

        if not response.is_success or not data.get("ok", False):
            error_code = data.get("error_code") or response.status_code
            params = data.get("parameters") or {}
            retry_after = params.get("retry_after", 3)
            raise TelegramApiError(error_code, retry_after)

        return data.get("result")


def split_telegram_text(text: str) -> list[str]:
    """Split text into chunks of at most 4000 characters without breaking characters."""
    chunks: list[str] = []
    chunk = ""
    for char in text:
        if len(chunk) + len(char) > 4000:
            chunks.append(chunk)
            chunk = ""
        chunk += char
    if chunk:
        chunks.append(chunk)
    return chunks


class TelegramAdapter:
    """Telegram Bot gateway adapter supporting polling, webhooks, and inline approvals."""

    def __init__(
        self,
        bus: MessageBus | None = None,
        memory: Any = None,
        api: TelegramTransport | None = None,
        allowed: set[str] | None = None,
        token: str = "",
        wait: Callable[[int, Any], Awaitable[None]] | None = None,
        approval_provider: ApprovalProvider | None = None,
        stt: SpeechToText | None = None,
        tts: TextToSpeech | None = None,
        reply_as_voice: bool = False,
        *,
        bot_token: str | None = None,
        allowed_users: list[str] | None = None,
    ) -> None:
        self.bot_token = bot_token
        self.allowed_users = allowed_users or []
        self.stt = stt
        self.tts = tts
        self.reply_as_voice = reply_as_voice

        if bus is None or api is None or allowed is None:
            # Placeholder initialization mode for backward compatibility
            return

        if not allowed:
            raise ValueError("TELEGRAM_ALLOWED_USER_IDS must not be empty")

        self.bus = bus
        self.memory = memory
        self.api = api
        self.allowed = {str(uid).strip() for uid in allowed if str(uid).strip()}
        if not self.allowed:
            raise ValueError("TELEGRAM_ALLOWED_USER_IDS must not be empty")

        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        self.state_key = f"telegram-offset:{token_hash}"
        self.wait_fn = wait
        self.approval_provider = approval_provider

        # In-flight approval callbacks and arguments
        self.pending_approvals: dict[str, Callable[[bool], None]] = {}
        self.active_calls: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        """Placeholder start method for backward compatibility."""
        raise NotImplementedError("Telegram adapter logic will be ported in subsequent phases.")

    async def stop(self) -> None:
        """Placeholder stop method for backward compatibility."""
        pass

    def _is_aborted(self, signal: Any) -> bool:
        if signal is None:
            return False
        if hasattr(signal, "is_set"):
            return bool(signal.is_set())
        if hasattr(signal, "aborted"):
            return bool(signal.aborted)
        return False

    async def _wait(self, ms: int, signal: Any) -> None:
        if self.wait_fn is not None:
            await self.wait_fn(ms, signal)
        else:
            await asyncio.sleep(ms / 1000.0)

    async def _get_memory_state(self, key: str) -> str | None:
        if hasattr(self.memory, "get_state"):
            fn = self.memory.get_state
        elif hasattr(self.memory, "getState"):
            fn = self.memory.getState
        else:
            return None

        res = fn(key)
        if inspect.isawaitable(res):
            return await res  # type: ignore[no-any-return]
        return res  # type: ignore[no-any-return]

    async def _set_memory_state(self, key: str, value: str) -> None:
        if hasattr(self.memory, "set_state"):
            fn = self.memory.set_state
        elif hasattr(self.memory, "setState"):
            fn = self.memory.setState
        else:
            return

        res = fn(key, value)
        if inspect.isawaitable(res):
            await res

    async def handle_callback_query(
        self,
        query: TelegramCallbackQuery | dict[str, Any],
        signal: Any = None,
    ) -> None:
        """Handle inline keyboard button interaction for ActionCard approval/denial."""
        if isinstance(query, dict):
            q = TelegramCallbackQuery.model_validate(query)
        else:
            q = query

        user_id = str(q.from_.id)
        if user_id not in self.allowed:
            try:
                await self.api.call(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": q.id,
                        "text": "Yetkisiz kullanıcı",
                        "show_alert": True,
                    },
                    signal=signal,
                )
            except Exception:
                pass
            return

        data = q.data or ""
        parts = data.split(":")
        if len(parts) != 3:
            try:
                await self.api.call(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": q.id,
                        "text": "Geçersiz onay isteği",
                        "show_alert": True,
                    },
                    signal=signal,
                )
            except Exception:
                pass
            return

        action, approval_id, nonce = parts

        if action == "approve":
            actual_args = self.active_calls.get(approval_id)
            if self.approval_provider is not None:
                result = self.approval_provider.consume(approval_id, nonce, actual_args)
                if not result.success:
                    try:
                        await self.api.call(
                            "answerCallbackQuery",
                            {
                                "callback_query_id": q.id,
                                "text": f"Onay reddedildi: {result.reason}",
                                "show_alert": True,
                            },
                            signal=signal,
                        )
                    except Exception:
                        pass
                    resolver = self.pending_approvals.get(approval_id)
                    if resolver:
                        resolver(False)
                    return

            try:
                await self.api.call(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": q.id,
                        "text": "✅ Eylem onaylandı ve çalıştırılıyor",
                    },
                    signal=signal,
                )
            except Exception:
                pass

            resolver = self.pending_approvals.get(approval_id)
            if resolver:
                resolver(True)
            return

        if action == "deny":
            if self.approval_provider is not None:
                self.approval_provider.deny(approval_id, nonce)

            try:
                await self.api.call(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": q.id,
                        "text": "❌ Eylem reddedildi",
                    },
                    signal=signal,
                )
            except Exception:
                pass

            resolver = self.pending_approvals.get(approval_id)
            if resolver:
                resolver(False)
            return

        if action == "details":
            info = "Kart bulunamadı"
            if self.approval_provider is not None:
                record = self.approval_provider.get(approval_id)
                if record:
                    effects_str = ", ".join(record.card.rendered.effects)
                    sha_prefix = record.card.args_sha256[:16]
                    info = f"Etkiler: {effects_str}\nArgs SHA: {sha_prefix}..."

            try:
                await self.api.call(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": q.id,
                        "text": info,
                        "show_alert": True,
                    },
                    signal=signal,
                )
            except Exception:
                pass
            return

    async def handle(
        self,
        update: TelegramUpdate | dict[str, Any],
        signal: Any = None,
    ) -> None:
        """Handle incoming Telegram update, routing text message or callback query."""
        if isinstance(update, dict):
            upd = TelegramUpdate.model_validate(update)
        else:
            upd = update

        if upd.callback_query is not None:
            await self.handle_callback_query(upd.callback_query, signal)
            return

        msg = upd.message
        if not msg or not msg.from_ or msg.from_.is_bot:
            return
        if msg.chat.type != "private":
            return
        if str(msg.from_.id) not in self.allowed:
            return

        text_content = msg.text
        is_voice_message = False
        voice_obj = msg.voice or msg.audio

        if text_content is None and voice_obj is not None:
            if self.stt is None:
                return
            is_voice_message = True
            file_info = await self.api.call(
                "getFile", {"file_id": voice_obj.file_id}, signal=signal
            )
            file_path: str | None = None
            if isinstance(file_info, dict):
                file_path = file_info.get("file_path")
            elif hasattr(file_info, "file_path"):
                file_path = file_info.file_path

            if not file_path:
                return

            audio_bytes = await self.api.download_file(file_path, signal=signal)
            transcription = await self.stt.transcribe(
                audio_bytes, mime_type=voice_obj.mime_type
            )
            text_content = transcription.text

        if not text_content:
            return

        async def approve_callback(call: Any, card: Any = None) -> bool:
            action_card: ActionCard | None = None
            if isinstance(card, ActionCard):
                action_card = card
            elif isinstance(card, dict) and ("approvalId" in card or "approval_id" in card):
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

            self.active_calls[action_card.approval_id] = input_args

            card_text = format_telegram_card_message(action_card)
            keyboard = build_telegram_card_keyboard(action_card)

            await self.api.call(
                "sendMessage",
                {
                    "chat_id": msg.chat.id,
                    "text": card_text,
                    "reply_markup": keyboard,
                },
                signal=signal,
            )

            loop = asyncio.get_running_loop()
            fut: asyncio.Future[bool] = loop.create_future()
            app_id = action_card.approval_id

            def cleanup() -> None:
                self.pending_approvals.pop(app_id, None)
                self.active_calls.pop(app_id, None)

            def on_timeout() -> None:
                if not fut.done():
                    cleanup()
                    fut.set_result(False)

            timer = loop.call_later(300.0, on_timeout)

            def resolve(approved: bool) -> None:
                timer.cancel()
                cleanup()
                if not fut.done():
                    fut.set_result(approved)

            self.pending_approvals[app_id] = resolve
            return await fut

        reply: str
        try:
            reply = await self.bus.dispatch(
                IncomingMessage(
                    channel="telegram",
                    userId=str(msg.from_.id),
                    conversationId=str(msg.chat.id),
                    requestId=str(upd.update_id),
                    text=text_content,
                ),
                approve=approve_callback,
                signal=signal,
            )
        except Exception:
            if self._is_aborted(signal):
                raise
            reply = (
                "Request failed. Check provider configuration and connectivity, "
                "then send your message again."
            )

        if (self.reply_as_voice or is_voice_message) and self.tts is not None:
            try:
                audio_res = await self.tts.synthesize(reply)
                await self.api.call(
                    "sendVoice",
                    {
                        "chat_id": msg.chat.id,
                        "caption": reply[:1024] if len(reply) <= 1024 else reply[:1020] + "...",
                        "voice_bytes": audio_res.audio_bytes,
                    },
                    signal=signal,
                )
                return
            except Exception:
                pass

        for chunk in split_telegram_text(reply):
            await self.api.call(
                "sendMessage",
                {"chat_id": msg.chat.id, "text": chunk},
                signal=signal,
            )

    async def handle_webhook(
        self,
        update_data: dict[str, Any] | TelegramUpdate,
        signal: Any = None,
    ) -> None:
        """Process an update delivered via webhook endpoint."""
        await self.handle(update_data, signal=signal)

    async def run(self, signal: Any = None) -> None:
        """Run long-polling update loop, persisting offsets and handling errors."""
        saved_offset_str = await self._get_memory_state(self.state_key)
        offset = 0
        if saved_offset_str is not None:
            try:
                offset = int(saved_offset_str)
                if offset < 0:
                    raise ValueError
            except ValueError as err:
                raise ValueError("Invalid saved Telegram offset") from err

        while not self._is_aborted(signal):
            try:
                raw_updates = await self.api.call(
                    "getUpdates",
                    {
                        "offset": offset,
                        "timeout": 30,
                        "allowed_updates": ["message", "callback_query"],
                    },
                    signal=signal,
                )
                if not isinstance(raw_updates, list):
                    raw_updates = []

                updates = [
                    TelegramUpdate.model_validate(u) if isinstance(u, dict) else u
                    for u in raw_updates
                ]

                for update in updates:
                    if self._is_aborted(signal):
                        break
                    if update.update_id < offset:
                        continue
                    await self.handle(update, signal=signal)
                    offset = update.update_id + 1
                    await self._set_memory_state(self.state_key, str(offset))

            except Exception as error:
                if self._is_aborted(signal):
                    break
                if isinstance(error, TelegramApiError) and error.code in (401, 403, 409):
                    raise error
                retry_seconds = (
                    error.retry_after if isinstance(error, TelegramApiError) else 3
                )
                retry_seconds = max(1, min(retry_seconds, 300))
                await self._wait(retry_seconds * 1000, signal)


__all__ = [
    "TelegramAdapter",
    "TelegramApi",
    "TelegramApiError",
    "TelegramCallbackQuery",
    "TelegramChat",
    "TelegramFrom",
    "TelegramMessage",
    "TelegramTransport",
    "TelegramUpdate",
    "TelegramVoice",
    "split_telegram_text",
]
