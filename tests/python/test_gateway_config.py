"""Tests for Gateway configuration schema and validation rules."""

import pytest

from yusufclaw.gateway.config import load_config


def test_gateway_config_bedrock_defaults_and_validation() -> None:
    """Config defaults to named Bedrock profile and rejects invalid config without values."""
    cfg1 = load_config({"BEDROCK_MODEL_ID": "us.anthropic.claude-3-5-sonnet"})
    assert cfg1.MODEL_PROVIDER == "bedrock"
    assert cfg1.AWS_PROFILE == "yusufclaw"
    assert cfg1.AWS_REGION == "us-east-1"
    assert cfg1.BEDROCK_MODEL_ID == "us.anthropic.claude-3-5-sonnet"

    cfg2 = load_config({"MODEL_PROVIDER": "ollama", "BEDROCK_MODEL_ID": ""})
    assert cfg2.MODEL_PROVIDER == "ollama"

    # Missing BEDROCK_MODEL_ID when provider is bedrock
    with pytest.raises(ValueError, match="BEDROCK_MODEL_ID"):
        load_config({})

    # MAX_AGENT_STEPS must be positive
    with pytest.raises(ValueError, match="MAX_AGENT_STEPS"):
        load_config({"MODEL_PROVIDER": "ollama", "MAX_AGENT_STEPS": "0"})

    # TELEGRAM_ALLOWED_USER_IDS must be numeric
    with pytest.raises(ValueError, match="numeric"):
        load_config({"MODEL_PROVIDER": "ollama", "TELEGRAM_ALLOWED_USER_IDS": "anyone"})

    # TELEGRAM_BOT_TOKEN must match format
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        load_config({"MODEL_PROVIDER": "ollama", "TELEGRAM_BOT_TOKEN": "SECRET"})


def test_gateway_config_openai_mode_validation() -> None:
    """Validates OpenAI env configuration and requires API key and model."""
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        load_config({"MODEL_PROVIDER": "openai"})

    cfg = load_config(
        {
            "MODEL_PROVIDER": "openai",
            "OPENAI_API_KEY": "fixture-key",
            "OPENAI_MODEL": "gpt-4o",
        }
    )
    assert cfg.WEB_HOST == "127.0.0.1"
    assert cfg.WEB_PORT == 3000
    assert cfg.OPENAI_BASE_URL == "https://api.openai.com/v1"
    assert cfg.OPENAI_API_KEY == "fixture-key"
    assert cfg.OPENAI_MODEL == "gpt-4o"


def test_gateway_config_ollama_url_validation() -> None:
    """OLLAMA_BASE_URL must use http or https scheme."""
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        load_config(
            {
                "MODEL_PROVIDER": "ollama",
                "OLLAMA_BASE_URL": "ftp://localhost:11434",
            }
        )
