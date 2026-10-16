"""Tests for YusufClaw pydantic-settings configuration."""

import os
from unittest.mock import patch

from yusufclaw.config import Settings, get_settings


def test_default_settings() -> None:
    """Verify default configuration values."""
    settings = Settings()
    assert settings.model_provider in {"fake", "bedrock", "ollama", "openai"}
    assert settings.aws_profile == "yusufclaw"
    assert settings.aws_region == "us-east-1"
    assert settings.max_agent_steps == 8
    assert settings.web_port == 3000
    assert settings.redis_url == "redis://127.0.0.1:6379/0"


def test_settings_env_override() -> None:
    """Verify settings loaded from environment variables."""
    env = {
        "MODEL_PROVIDER": "ollama",
        "OLLAMA_MODEL": "mistral",
        "MAX_AGENT_STEPS": "12",
        "TELEGRAM_ALLOWED_USER_IDS": "12345, 67890",
    }
    with patch.dict(os.environ, env, clear=False):
        settings = Settings()
        assert settings.model_provider == "ollama"
        assert settings.ollama_model == "mistral"
        assert settings.max_agent_steps == 12
        assert settings.allowed_telegram_users_list == ["12345", "67890"]


def test_get_settings_cached() -> None:
    """Verify get_settings returns a cached instance."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
