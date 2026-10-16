"""Channel adapter registry and wiring hooks for YusufClaw gateway.

Discovers, configures, and registers inbound/outbound channel adapters (Telegram, WhatsApp, CLI)
and the speech/voice subsystem on the unified MessageBus. Channels are disabled unless configured.
"""

from __future__ import annotations

from typing import Any

from yusufclaw.gateway.approval import ApprovalProvider
from yusufclaw.gateway.bus import MessageBus
from yusufclaw.gateway.config import GatewayConfig
from yusufclaw.gateway.telegram import TelegramAdapter, TelegramApi
from yusufclaw.gateway.whatsapp import WhatsAppAdapter, WhatsAppApi
from yusufclaw.voice.factory import get_stt_provider, get_tts_provider
from yusufclaw.voice.types import SpeechToText, TextToSpeech


class ChannelRegistry:
    """Registry holding initialized and active gateway channel adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, Any] = {}

    def register(self, name: str, adapter: Any) -> None:
        """Register a channel adapter by name."""
        self._adapters[name] = adapter

    def get(self, name: str) -> Any | None:
        """Retrieve registered channel adapter by name."""
        return self._adapters.get(name)

    def has(self, name: str) -> bool:
        """Check whether a channel adapter is registered."""
        return name in self._adapters

    def list_channels(self) -> list[str]:
        """List registered channel names."""
        return list(self._adapters.keys())


def setup_channels(
    config: GatewayConfig | Any,
    bus: MessageBus,
    memory: Any = None,
    approval_provider: ApprovalProvider | None = None,
    *,
    stt: SpeechToText | None = None,
    tts: TextToSpeech | None = None,
) -> ChannelRegistry:
    """Wire and register configured gateway channels according to application settings.

    Channels remain disabled unless all required credentials and allowlists are provided.
    Speech STT/TTS are instantiated if voice is enabled or passed explicitly.
    """
    registry = ChannelRegistry()

    # 1. Setup speech/voice providers if configured
    voice_enabled = getattr(config, "VOICE_ENABLED", False) or getattr(
        config, "voice_enabled", False
    )
    stt_provider = stt
    tts_provider = tts

    if voice_enabled:
        if stt_provider is None:
            raw_stt_name = getattr(config, "VOICE_STT_PROVIDER", None) or getattr(
                config, "voice_stt_provider", "fake"
            )
            stt_name = str(raw_stt_name) if raw_stt_name else "fake"
            stt_kwargs: dict[str, Any] = {}
            if stt_name == "openai":
                stt_kwargs["model"] = getattr(
                    config, "OPENAI_STT_MODEL", None
                ) or getattr(config, "openai_stt_model", "whisper-1")
            elif stt_name in ("whisper", "faster-whisper"):
                stt_kwargs["model_size_or_path"] = getattr(
                    config, "WHISPER_MODEL_SIZE", None
                ) or getattr(config, "whisper_model_size", "base")
            stt_provider = get_stt_provider(stt_name, **stt_kwargs)

        if tts_provider is None:
            raw_tts_name = getattr(config, "VOICE_TTS_PROVIDER", None) or getattr(
                config, "voice_tts_provider", "fake"
            )
            tts_name = str(raw_tts_name) if raw_tts_name else "fake"
            tts_kwargs: dict[str, Any] = {}
            if tts_name == "openai":
                tts_kwargs["model"] = getattr(
                    config, "OPENAI_TTS_MODEL", None
                ) or getattr(config, "openai_tts_model", "tts-1")
                tts_kwargs["voice"] = getattr(
                    config, "OPENAI_TTS_VOICE", None
                ) or getattr(config, "openai_tts_voice", "alloy")
            elif tts_name in ("amazon", "polly"):
                tts_kwargs["voice_id"] = getattr(
                    config, "POLLY_VOICE_ID", None
                ) or getattr(config, "polly_voice_id", "Joanna")
            tts_provider = get_tts_provider(tts_name, **tts_kwargs)

    reply_as_voice = getattr(config, "VOICE_REPLY_AS_AUDIO", False) or getattr(
        config, "voice_reply_as_audio", False
    )

    # 2. Wire Telegram channel (disabled unless token and allowed user IDs are set)
    telegram_token = getattr(config, "TELEGRAM_BOT_TOKEN", None) or getattr(
        config, "telegram_bot_token", None
    )
    telegram_allowed = getattr(config, "allowed_ids", None) or getattr(
        config, "allowed_telegram_users_list", []
    )
    if telegram_token and telegram_allowed:
        telegram_api = TelegramApi(telegram_token)
        telegram_adapter = TelegramAdapter(
            bus=bus,
            memory=memory,
            api=telegram_api,
            allowed=set(telegram_allowed),
            token=telegram_token,
            approval_provider=approval_provider,
            stt=stt_provider,
            tts=tts_provider,
            reply_as_voice=reply_as_voice,
        )
        registry.register("telegram", telegram_adapter)

    # 3. Wire WhatsApp channel (disabled unless token, phone number ID, and allowed numbers are set)
    whatsapp_token = getattr(config, "WHATSAPP_ACCESS_TOKEN", None) or getattr(
        config, "whatsapp_access_token", None
    )
    whatsapp_phone_id = getattr(config, "WHATSAPP_PHONE_NUMBER_ID", None) or getattr(
        config, "whatsapp_phone_number_id", None
    )
    whatsapp_allowed = getattr(
        config, "allowed_whatsapp_numbers", None
    ) or getattr(config, "allowed_whatsapp_numbers_list", [])
    whatsapp_secret = getattr(config, "WHATSAPP_APP_SECRET", "") or getattr(
        config, "whatsapp_app_secret", ""
    ) or ""
    whatsapp_verify = getattr(config, "WHATSAPP_VERIFY_TOKEN", "") or getattr(
        config, "whatsapp_verify_token", ""
    ) or ""

    if whatsapp_token and whatsapp_phone_id and whatsapp_allowed:
        whatsapp_api = WhatsAppApi(
            access_token=whatsapp_token,
            phone_number_id=whatsapp_phone_id,
        )
        whatsapp_adapter = WhatsAppAdapter(
            bus=bus,
            api=whatsapp_api,
            allowed_numbers=set(whatsapp_allowed),
            verify_token=whatsapp_verify,
            app_secret=whatsapp_secret,
            approval_provider=approval_provider,
            stt=stt_provider,
            tts=tts_provider,
            reply_as_voice=reply_as_voice,
        )
        registry.register("whatsapp", whatsapp_adapter)

    return registry


__all__ = ["ChannelRegistry", "setup_channels"]
