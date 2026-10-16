"""Models subpackage for YusufClaw.

Provides standardized model interfaces and implementations for:
- Ollama (local open-weights models)
- OpenAI (compatible Chat Completions endpoints)
- AWS Bedrock (Converse API via boto3)
- Fake provider (deterministic test and offline execution)
"""

from collections.abc import Mapping

from yusufclaw.config import Settings, get_settings
from yusufclaw.models.bedrock import BedrockProvider
from yusufclaw.models.fake import FakeProvider, FakeStreamingProvider
from yusufclaw.models.ollama import OllamaProvider
from yusufclaw.models.openai import OpenAIProvider
from yusufclaw.models.router import ModelRole, ModelRouter, RoutingConfig
from yusufclaw.models.types import (
    Message,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    Role,
    StreamAccumulator,
    StreamChunk,
    ToolCall,
    ToolCallDelta,
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


def get_model_router(
    settings: Settings | None = None,
    routing_config: RoutingConfig | None = None,
    providers: Mapping[str, ModelProvider] | None = None,
) -> ModelRouter:
    """Factory creating the configured model router instance."""
    cfg = settings or get_settings()
    rc = routing_config or RoutingConfig(
        enabled=getattr(cfg, "enable_model_routing", False),
        default_provider=getattr(cfg, "routing_default_provider", "default"),
        strong_provider=getattr(cfg, "routing_strong_provider", "strong"),
        cheap_provider=getattr(cfg, "routing_cheap_provider", "cheap"),
        prefer_cheap_for_simple=getattr(cfg, "routing_prefer_cheap_for_simple", False),
    )

    if providers is not None:
        return ModelRouter(providers=providers, config=rc)

    if cfg.model_provider == "fake":
        fake = FakeProvider()
        return ModelRouter(
            providers={"default": fake, "fake": fake, "strong": fake, "cheap": fake},
            default_provider=fake,
            config=rc,
        )

    # Multi-provider setup from settings
    registered: dict[str, ModelProvider] = {}
    default_p = get_model_provider(cfg)
    registered["default"] = default_p
    registered[cfg.model_provider] = default_p

    # Ollama as cheap / local provider if configured or available
    if cfg.model_provider != "ollama" and cfg.ollama_base_url:
        try:
            registered["ollama"] = OllamaProvider(
                base_url=cfg.ollama_base_url,
                model=cfg.ollama_model,
                timeout_ms=cfg.model_timeout_ms,
            )
            registered["cheap"] = registered["ollama"]
        except Exception:
            pass

    if "cheap" not in registered:
        registered["cheap"] = default_p
    if "strong" not in registered:
        registered["strong"] = default_p

    return ModelRouter(providers=registered, default_provider=default_p, config=rc)


__all__ = [
    "BedrockProvider",
    "FakeProvider",
    "FakeStreamingProvider",
    "Message",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "ModelRole",
    "ModelRouter",
    "OllamaProvider",
    "OpenAIProvider",
    "Role",
    "RoutingConfig",
    "StreamAccumulator",
    "StreamChunk",
    "ToolCall",
    "ToolCallDelta",
    "ToolDefinition",
    "get_model_provider",
    "get_model_router",
]
