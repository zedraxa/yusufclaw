"""Models subpackage for YusufClaw.

Provides standardized model interfaces and implementations for:
- Ollama (local open-weights models)
- OpenAI (compatible Chat Completions endpoints)
- AWS Bedrock (Converse API via boto3)
- Fake provider (deterministic test and offline execution)
"""

from yusufclaw.config import Settings, get_settings
from yusufclaw.models.bedrock import BedrockProvider
from yusufclaw.models.fake import FakeProvider
from yusufclaw.models.ollama import OllamaProvider
from yusufclaw.models.openai import OpenAIProvider
from yusufclaw.models.types import (
    Message,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    Role,
    ToolCall,
    ToolDefinition,
)


def get_model_provider(settings: Settings | None = None) -> ModelProvider:
    """Factory creating the configured model provider instance."""
    cfg = settings or get_settings()

    if cfg.model_provider == "fake":
        return FakeProvider()
    elif cfg.model_provider == "ollama":
        return OllamaProvider(
            base_url=cfg.ollama_base_url,
            model=cfg.ollama_model,
            timeout_ms=cfg.model_timeout_ms,
        )
    elif cfg.model_provider == "openai":
        return OpenAIProvider(
            base_url=cfg.openai_base_url,
            api_key=cfg.openai_api_key,
            model=cfg.openai_model,
            timeout_ms=cfg.model_timeout_ms,
        )
    elif cfg.model_provider == "bedrock":
        return BedrockProvider(
            model_id=cfg.bedrock_model_id,
            profile=cfg.aws_profile,
            region=cfg.aws_region,
            max_tokens=cfg.max_tokens,
        )
    else:
        raise ValueError(f"Unsupported model provider: {cfg.model_provider}")


__all__ = [
    "BedrockProvider",
    "FakeProvider",
    "Message",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "OllamaProvider",
    "OpenAIProvider",
    "Role",
    "ToolCall",
    "ToolDefinition",
    "get_model_provider",
]
