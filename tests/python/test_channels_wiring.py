"""Tests for channel registry, setup_channels wiring, and gateway main mode dispatching."""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import MagicMock

import pytest

from yusufclaw.config import Settings
from yusufclaw.gateway.bus import MessageBus
from yusufclaw.gateway.channels import ChannelRegistry, setup_channels
from yusufclaw.gateway.config import GatewayConfig
from yusufclaw.gateway.main import async_main
from yusufclaw.gateway.telegram import TelegramAdapter
from yusufclaw.gateway.whatsapp import WhatsAppAdapter
from yusufclaw.voice import FakeSTT, FakeTTS


class DummyAgent:
    async def handle(self, *args: Any, **kwargs: Any) -> str:
        return "dummy reply"


def test_channel_registry_basic_operations() -> None:
    registry = ChannelRegistry()
    assert registry.list_channels() == []
    assert registry.has("telegram") is False
    assert registry.get("telegram") is None

    mock_adapter = MagicMock()
    registry.register("telegram", mock_adapter)
    assert registry.has("telegram") is True
    assert registry.get("telegram") is mock_adapter
    assert registry.list_channels() == ["telegram"]


def test_setup_channels_disabled_by_default() -> None:
    """When channels are unconfigured, they remain disabled."""
    config = GatewayConfig(MODEL_PROVIDER="fake")
    bus = MessageBus(DummyAgent(), cli={"local"})
    registry = setup_channels(config, bus)

    assert registry.has("telegram") is False
    assert registry.has("whatsapp") is False
    assert registry.list_channels() == []


def test_setup_channels_with_telegram_configured() -> None:
    """When Telegram is configured, telegram adapter is registered."""
    config = GatewayConfig(
        MODEL_PROVIDER="fake",
        TELEGRAM_BOT_TOKEN="123456:ABC-DEF_ghi",
        TELEGRAM_ALLOWED_USER_IDS="111,222",
    )
    bus = MessageBus(DummyAgent(), telegram={"111", "222"})
    registry = setup_channels(config, bus)

    assert registry.has("telegram") is True
    assert registry.has("whatsapp") is False
    adapter = registry.get("telegram")
    assert isinstance(adapter, TelegramAdapter)
    assert "111" in adapter.allowed
    assert "222" in adapter.allowed


def test_setup_channels_with_whatsapp_configured() -> None:
    """When WhatsApp is configured, whatsapp adapter is registered."""
    config = GatewayConfig(
        MODEL_PROVIDER="fake",
        WHATSAPP_ACCESS_TOKEN="mock_token",
        WHATSAPP_PHONE_NUMBER_ID="123456789",
        WHATSAPP_ALLOWED_NUMBERS="905551234567,15550234567",
    )
    bus = MessageBus(DummyAgent(), whatsapp={"905551234567", "15550234567"})
    registry = setup_channels(config, bus)

    assert registry.has("whatsapp") is True
    assert registry.has("telegram") is False
    adapter = registry.get("whatsapp")
    assert isinstance(adapter, WhatsAppAdapter)
    assert "905551234567" in adapter.allowed
    assert "15550234567" in adapter.allowed


def test_setup_channels_with_voice_subsystem() -> None:
    """When voice is enabled, speech providers are wired into channel adapters."""
    config = GatewayConfig(
        MODEL_PROVIDER="fake",
        TELEGRAM_BOT_TOKEN="123456:ABC-DEF_ghi",
        TELEGRAM_ALLOWED_USER_IDS="111",
        WHATSAPP_ACCESS_TOKEN="mock_token",
        WHATSAPP_PHONE_NUMBER_ID="123456789",
        WHATSAPP_ALLOWED_NUMBERS="905551234567",
        VOICE_ENABLED=True,
        VOICE_STT_PROVIDER="fake",
        VOICE_TTS_PROVIDER="fake",
        VOICE_REPLY_AS_AUDIO=True,
    )
    bus = MessageBus(DummyAgent(), telegram={"111"}, whatsapp={"905551234567"})
    registry = setup_channels(config, bus)

    tg_adapter = registry.get("telegram")
    wa_adapter = registry.get("whatsapp")

    assert isinstance(tg_adapter, TelegramAdapter)
    assert isinstance(wa_adapter, WhatsAppAdapter)
    assert isinstance(tg_adapter.stt, FakeSTT)
    assert isinstance(tg_adapter.tts, FakeTTS)
    assert tg_adapter.reply_as_voice is True
    assert isinstance(wa_adapter.stt, FakeSTT)
    assert isinstance(wa_adapter.tts, FakeTTS)
    assert wa_adapter.reply_as_voice is True


def test_setup_channels_with_pydantic_settings() -> None:
    """setup_channels also accepts core Settings model."""
    settings = Settings(
        whatsapp_access_token="wa_token_abc",
        whatsapp_phone_number_id="wa_phone_123",
        whatsapp_allowed_numbers="905559876543",
        voice_enabled=True,
    )
    bus = MessageBus(DummyAgent(), whatsapp={"905559876543"})
    registry = setup_channels(settings, bus)

    assert registry.has("whatsapp") is True
    wa_adapter = registry.get("whatsapp")
    assert isinstance(wa_adapter, WhatsAppAdapter)
    assert "905559876543" in wa_adapter.allowed


@pytest.mark.asyncio
async def test_gateway_main_whatsapp_mode_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests gateway async_main mode validation for whatsapp."""
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)
    monkeypatch.delenv("WHATSAPP_ALLOWED_NUMBERS", raising=False)

    with pytest.raises(ValueError, match="WhatsApp requires access token"):
        await async_main(["whatsapp"])

    # 2. Invalid mode raises ValueError
    with pytest.raises(ValueError, match="Choose cli, telegram, web, routines, or whatsapp"):
        await async_main(["invalid_mode"])

    # 3. --help prints whatsapp mode
    stdout_buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", stdout_buf)
    await async_main(["--help"])
    help_text = stdout_buf.getvalue()
    assert "yusufclaw whatsapp" in help_text
