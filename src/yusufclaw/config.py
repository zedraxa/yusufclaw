"""Configuration management for YusufClaw using pydantic-settings.

Loads environment variables, .env files, and provides strongly-typed settings
for models, gateway channels, database connections, and operational limits.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ModelProviderType = Literal["bedrock", "ollama", "openai", "fake"]


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Model Provider Settings
    model_provider: ModelProviderType = Field(
        default="fake",
        description="Active model provider (bedrock, ollama, openai, fake).",
    )
    aws_profile: str = Field(default="yusufclaw", description="AWS profile name for Bedrock.")
    aws_region: str = Field(default="us-east-1", description="AWS region for Bedrock.")
    bedrock_model_id: str = Field(default="", description="AWS Bedrock model ID or inference profile ARN.")

    ollama_base_url: str = Field(default="http://127.0.0.1:11434", description="Ollama server URL.")
    ollama_model: str = Field(default="llama3.2", description="Ollama model tag.")

    openai_base_url: str = Field(default="https://api.openai.com/v1", description="OpenAI base API URL.")
    openai_api_key: str | None = Field(default=None, description="API key for OpenAI-compatible endpoints.")
    openai_model: str = Field(default="gpt-4o-mini", description="Model name for OpenAI.")

    # Model Routing Settings
    enable_model_routing: bool = Field(default=False, description="Enable role-based adaptive model routing.")
    routing_default_provider: str = Field(default="default", description="Default provider name for routing.")
    routing_strong_provider: str = Field(default="strong", description="Strong provider name (bedrock, openai, strong).")
    routing_cheap_provider: str = Field(default="cheap", description="Cheap/local provider name (ollama, cheap).")
    routing_prefer_cheap_for_simple: bool = Field(default=False, description="Prefer cheap provider for simple, low-risk turns.")

    # Model Execution Limits
    max_tokens: int = Field(default=2048, ge=1, description="Max response tokens.")
    model_timeout_ms: int = Field(default=120000, ge=1000, description="Model request timeout in ms.")
    max_agent_steps: int = Field(default=8, ge=1, le=50, description="Maximum agent tool-calling loop steps.")
    history_turns: int = Field(default=20, ge=1, description="Number of past conversation turns to retain.")

    # Storage and Memory
    memory_path: str = Field(default=".data/memory.sqlite", description="Path to local SQLite memory file.")
    database_url: str = Field(
        default="postgresql://postgres:postgres@127.0.0.1:5432/yusufclaw",
        description="PostgreSQL connection URL (with pgvector).",
    )
    redis_url: str = Field(default="redis://127.0.0.1:6379/0", description="Redis connection URL.")

    # CLI & Web Gateway
    cli_user_id: str = Field(default="local", description="User identifier for CLI chat session.")
    web_host: str = Field(default="127.0.0.1", description="Web gateway listening host.")
    web_port: int = Field(default=3000, ge=1, le=65535, description="Web gateway listening port.")
    web_user_id: str = Field(default="local", description="Default user identifier for web chat.")
    web_auth_token: str | None = Field(default=None, description="Optional bearer token for web API auth.")

    # Telegram Gateway
    telegram_bot_token: str | None = Field(default=None, description="Telegram Bot API token.")
    telegram_allowed_user_ids: str = Field(
        default="",
        description="Comma-separated allowlist of Telegram numeric user IDs.",
    )

    # WhatsApp Gateway
    whatsapp_enabled: bool = Field(default=False, description="Enable WhatsApp Business Cloud API adapter.")
    whatsapp_access_token: str | None = Field(default=None, description="WhatsApp Cloud API access token.")
    whatsapp_phone_number_id: str | None = Field(default=None, description="WhatsApp Business phone number ID.")
    whatsapp_app_secret: str | None = Field(default=None, description="Meta App Secret for HMAC verification.")
    whatsapp_verify_token: str | None = Field(default=None, description="Meta webhook verify token.")
    whatsapp_allowed_numbers: str = Field(
        default="",
        description="Comma-separated allowlist of WhatsApp phone numbers (E.164).",
    )

    # Voice Subsystem
    voice_enabled: bool = Field(default=False, description="Enable speech transcription and voice synthesis.")
    voice_stt_provider: str = Field(default="fake", description="STT provider (fake, openai, whisper, amazon).")
    voice_tts_provider: str = Field(default="fake", description="TTS provider (fake, openai, amazon).")
    voice_reply_as_audio: bool = Field(default=False, description="Return replies as voice/audio messages.")
    openai_stt_model: str = Field(default="whisper-1", description="OpenAI transcription model.")
    openai_tts_model: str = Field(default="tts-1", description="OpenAI speech synthesis model.")
    openai_tts_voice: str = Field(default="alloy", description="OpenAI speech voice.")
    whisper_model_size: str = Field(default="base", description="faster-whisper model size.")
    polly_voice_id: str = Field(default="Joanna", description="Amazon Polly voice ID.")

    # Skills & Routines
    skills_dir: str = Field(default="skills", description="Directory containing local markdown skills.")
    routines_config: str | None = Field(default=None, description="Optional JSON path for scheduled routines.")

    # Telemetry
    otel_service_name: str = Field(default="yusufclaw", description="OpenTelemetry service name.")
    otel_exporter_otlp_endpoint: str | None = Field(
        default=None,
        description="Optional OTLP endpoint for exporting traces and metrics.",
    )

    @property
    def allowed_telegram_users_list(self) -> list[str]:
        """Parse comma-separated telegram allowlist into trimmed strings."""
        if not self.telegram_allowed_user_ids:
            return []
        return [uid.strip() for uid in self.telegram_allowed_user_ids.split(",") if uid.strip()]

    @property
    def allowed_whatsapp_numbers_list(self) -> list[str]:
        """Parse comma-separated WhatsApp allowlist into trimmed normalized numbers."""
        import re

        if not self.whatsapp_allowed_numbers:
            return []
        return [
            re.sub(r"\D", "", num)
            for num in self.whatsapp_allowed_numbers.split(",")
            if re.sub(r"\D", "", num)
        ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()
