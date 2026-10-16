"""Configuration loader and schema for YusufClaw Gateway."""

from __future__ import annotations

import os
import re
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ModelProviderName = Literal["bedrock", "ollama", "openai", "fake"]

TELEGRAM_TOKEN_REGEX = re.compile(r"^\d+:[A-Za-z0-9_-]+$")


class GatewayConfig(BaseModel):
    """Strongly-typed gateway configuration matching TypeScript spec with Pydantic."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    MODEL_PROVIDER: ModelProviderName = "bedrock"
    BEDROCK_MODEL_ID: str | None = None
    AWS_PROFILE: str = "yusufclaw"
    AWS_REGION: str = "us-east-1"
    OLLAMA_MODEL: str = "llama3.2"
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str | None = None
    WEB_HOST: str = "127.0.0.1"
    WEB_PORT: int = Field(default=3000, ge=1, le=65535)
    WEB_AUTH_TOKEN: str | None = None
    WEB_USER_ID: str = "local"
    SKILLS_DIR: str = "skills"
    ROUTINES_CONFIG: str | None = None
    MEMORY_PATH: str = ".data/memory.sqlite"
    MAX_TOKENS: int = Field(default=2048, ge=1, le=65536)
    MODEL_TIMEOUT_MS: int = Field(default=120000, ge=1, le=600000)
    MAX_AGENT_STEPS: int = Field(default=8, ge=1, le=32)
    HISTORY_TURNS: int = Field(default=20, ge=1, le=100)
    CLI_USER_ID: str = "local"
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_ALLOWED_USER_IDS: str = ""

    allowed_ids: list[str] = Field(default_factory=list, alias="allowedIds")

    # WhatsApp Business Cloud API Settings
    WHATSAPP_ACCESS_TOKEN: str | None = None
    WHATSAPP_PHONE_NUMBER_ID: str | None = None
    WHATSAPP_APP_SECRET: str | None = None
    WHATSAPP_VERIFY_TOKEN: str | None = None
    WHATSAPP_ALLOWED_NUMBERS: str = ""
    allowed_whatsapp_numbers: list[str] = Field(
        default_factory=list, alias="allowedWhatsappNumbers"
    )

    # Voice Subsystem Settings
    VOICE_ENABLED: bool = False
    VOICE_STT_PROVIDER: str = "fake"
    VOICE_TTS_PROVIDER: str = "fake"
    VOICE_REPLY_AS_AUDIO: bool = False
    OPENAI_STT_MODEL: str = "whisper-1"
    OPENAI_TTS_MODEL: str = "tts-1"
    OPENAI_TTS_VOICE: str = "alloy"
    WHISPER_MODEL_SIZE: str = "base"
    POLLY_VOICE_ID: str = "Joanna"

    @property
    def allowedIds(self) -> list[str]:
        return self.allowed_ids

    @property
    def allowedWhatsappNumbers(self) -> list[str]:
        return self.allowed_whatsapp_numbers

    @field_validator("TELEGRAM_BOT_TOKEN", mode="before")
    @classmethod
    def validate_telegram_token(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        if isinstance(v, str):
            v_str = v.strip()
            if not TELEGRAM_TOKEN_REGEX.match(v_str):
                raise ValueError("Invalid configuration: TELEGRAM_BOT_TOKEN")
            return v_str
        raise ValueError("Invalid configuration: TELEGRAM_BOT_TOKEN")

    @model_validator(mode="before")
    @classmethod
    def preprocess_env(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if d.get("BEDROCK_MODEL_ID") == "":
            d["BEDROCK_MODEL_ID"] = None
        if d.get("OPENAI_API_KEY") == "":
            d["OPENAI_API_KEY"] = None
        if d.get("OPENAI_MODEL") == "":
            d["OPENAI_MODEL"] = None
        if d.get("TELEGRAM_BOT_TOKEN") == "":
            d["TELEGRAM_BOT_TOKEN"] = None
        if d.get("WHATSAPP_ACCESS_TOKEN") == "":
            d["WHATSAPP_ACCESS_TOKEN"] = None
        if d.get("WHATSAPP_PHONE_NUMBER_ID") == "":
            d["WHATSAPP_PHONE_NUMBER_ID"] = None
        if d.get("WHATSAPP_APP_SECRET") == "":
            d["WHATSAPP_APP_SECRET"] = None
        if d.get("WHATSAPP_VERIFY_TOKEN") == "":
            d["WHATSAPP_VERIFY_TOKEN"] = None
        return d

    @model_validator(mode="after")
    def validate_rules(self) -> GatewayConfig:
        if self.MODEL_PROVIDER == "bedrock" and not self.BEDROCK_MODEL_ID:
            raise ValueError("BEDROCK_MODEL_ID is required for Bedrock")
        if self.MODEL_PROVIDER == "openai" and (
            not self.OPENAI_API_KEY or not self.OPENAI_MODEL
        ):
            raise ValueError(
                "OPENAI_API_KEY and OPENAI_MODEL are required for OpenAI-compatible mode"
            )

        raw_ids = (
            self.TELEGRAM_ALLOWED_USER_IDS.split(",")
            if self.TELEGRAM_ALLOWED_USER_IDS
            else []
        )
        allowed = [uid.strip() for uid in raw_ids if uid.strip()]
        for uid in allowed:
            if not uid.isdigit() or int(uid) <= 0:
                raise ValueError("TELEGRAM_ALLOWED_USER_IDS must contain numeric user IDs")
        self.allowed_ids = allowed

        raw_wa = (
            self.WHATSAPP_ALLOWED_NUMBERS.split(",")
            if self.WHATSAPP_ALLOWED_NUMBERS
            else []
        )
        self.allowed_whatsapp_numbers = [
            re.sub(r"\D", "", num) for num in raw_wa if re.sub(r"\D", "", num)
        ]

        parsed_url = urlparse(self.OLLAMA_BASE_URL)
        if parsed_url.scheme not in ("http", "https"):
            raise ValueError("OLLAMA_BASE_URL must use HTTP or HTTPS")

        return self


def load_config(env: dict[str, Any] | None = None) -> GatewayConfig:
    """Load and validate configuration from dict or environment."""
    source = dict(os.environ) if env is None else env
    try:
        return GatewayConfig.model_validate(source)
    except Exception as exc:
        msg = str(exc)
        if "BEDROCK_MODEL_ID" in msg:
            raise ValueError("BEDROCK_MODEL_ID is required for Bedrock") from exc
        if "OPENAI_API_KEY" in msg:
            raise ValueError(
                "OPENAI_API_KEY and OPENAI_MODEL are required for OpenAI-compatible mode"
            ) from exc
        if "TELEGRAM_ALLOWED_USER_IDS" in msg or "numeric" in msg:
            raise ValueError("TELEGRAM_ALLOWED_USER_IDS must contain numeric user IDs") from exc
        if "TELEGRAM_BOT_TOKEN" in msg:
            raise ValueError("Invalid configuration: TELEGRAM_BOT_TOKEN") from exc
        if "MAX_AGENT_STEPS" in msg:
            raise ValueError("MAX_AGENT_STEPS must be positive") from exc
        if "OLLAMA_BASE_URL" in msg:
            raise ValueError("OLLAMA_BASE_URL must use HTTP or HTTPS") from exc
        raise


loadConfig = load_config

__all__ = ["GatewayConfig", "loadConfig", "load_config"]
