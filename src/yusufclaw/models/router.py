"""Adaptive model router for role-based provider selection and telemetry.

Implements Layer 2 model routing from docs/ARCHITECTURE-v2.md:
- Strong model for planning, synthesis, judge/verification.
- Cheap/local model (e.g. Ollama) for task classification, search, transform, heuristic assist.
- Dynamic selection based on TaskAnalysis (e.g. complexity=simple and risk=low -> cheap path).
- Offline single-provider fallback for deterministic test execution with FakeProvider.
- OpenTelemetry distributed tracing attribute emission.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from yusufclaw.models.types import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
)
from yusufclaw.telemetry.instrumentation import record_routing_decision

if TYPE_CHECKING:
    from yusufclaw.orchestration.models import TaskAnalysis

logger = logging.getLogger(__name__)


class ModelRole(StrEnum):
    """Functional roles for routing model requests."""

    PLANNER = "planner"
    SYNTHESIS = "synthesis"
    JUDGE = "judge"
    CLASSIFY = "classify"
    SEARCH = "search"
    TRANSFORM = "transform"
    HEURISTIC_ASSIST = "heuristic-assist"
    DEFAULT = "default"
    STRONG = "strong"
    CHEAP = "cheap"


class RoutingConfig(BaseModel):
    """Configuration for role-based adaptive model routing."""

    enabled: bool = False
    default_provider: str = "default"
    strong_provider: str = "strong"
    cheap_provider: str = "cheap"
    role_routes: dict[str, str] = Field(
        default_factory=lambda: {
            ModelRole.PLANNER.value: "strong",
            ModelRole.SYNTHESIS.value: "strong",
            ModelRole.JUDGE.value: "strong",
            ModelRole.CLASSIFY.value: "cheap",
            ModelRole.SEARCH.value: "cheap",
            ModelRole.TRANSFORM.value: "cheap",
            ModelRole.HEURISTIC_ASSIST.value: "cheap",
            ModelRole.DEFAULT.value: "default",
            ModelRole.STRONG.value: "strong",
            ModelRole.CHEAP.value: "cheap",
        }
    )
    prefer_cheap_for_simple: bool = False

    @classmethod
    def from_env(cls) -> RoutingConfig:
        """Construct RoutingConfig from YC_ROUTING_* environment variables."""
        enabled = os.getenv("YC_ROUTING_ENABLED", "").strip().lower() in ("1", "true", "yes")
        default_p = os.getenv("YC_ROUTING_DEFAULT_PROVIDER", "default").strip()
        strong_p = os.getenv("YC_ROUTING_STRONG_PROVIDER", "strong").strip()
        cheap_p = os.getenv("YC_ROUTING_CHEAP_PROVIDER", "cheap").strip()
        prefer_cheap = os.getenv("YC_ROUTING_PREFER_CHEAP_FOR_SIMPLE", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )

        routes_env = os.getenv("YC_ROUTING_ROLE_ROUTES")
        parsed_routes: dict[str, str] | None = None
        if routes_env:
            try:
                parsed_routes = json.loads(routes_env)
            except Exception:
                parsed_routes = {}
                for part in routes_env.split(","):
                    if ":" in part:
                        r, p = part.split(":", 1)
                        parsed_routes[r.strip()] = p.strip()

        default_routes = cls().role_routes
        if parsed_routes:
            default_routes.update(parsed_routes)

        return cls(
            enabled=enabled,
            default_provider=default_p,
            strong_provider=strong_p,
            cheap_provider=cheap_p,
            role_routes=default_routes,
            prefer_cheap_for_simple=prefer_cheap,
        )


class ModelRouter(ModelProvider):
    """Adaptive model router managing multiple named providers with fallback."""

    def __init__(
        self,
        providers: Mapping[str, ModelProvider] | None = None,
        default_provider: ModelProvider | None = None,
        config: RoutingConfig | None = None,
    ) -> None:
        self.config = config or RoutingConfig()
        self._providers: dict[str, ModelProvider] = dict(providers) if providers else {}
        self._default_provider: ModelProvider | None = default_provider

        if default_provider is not None:
            if "default" not in self._providers:
                self._providers["default"] = default_provider
        elif "default" in self._providers:
            self._default_provider = self._providers["default"]
        elif self._providers:
            # First registered provider becomes fallback
            self._default_provider = next(iter(self._providers.values()))

    @property
    def registered_providers(self) -> dict[str, ModelProvider]:
        """Return a copy of registered providers map."""
        return dict(self._providers)

    def register_provider(self, name: str, provider: ModelProvider) -> None:
        """Register or override a provider by name."""
        self._providers[name] = provider
        if self._default_provider is None:
            self._default_provider = provider

    def set_route(self, role: ModelRole | str, provider_name: str) -> None:
        """Map a role to a registered provider name."""
        role_str = role.value if isinstance(role, ModelRole) else str(role)
        self.config.role_routes[role_str] = provider_name

    def resolve_provider_name(
        self,
        role: ModelRole | str = ModelRole.DEFAULT,
        analysis: TaskAnalysis | None = None,
    ) -> tuple[str, str]:
        """Resolve the target provider name and reasoning string.

        Args:
            role: Explicit ModelRole or string identifier.
            analysis: Optional TaskAnalysis result to adaptively shift roles.

        Returns:
            Tuple of (resolved_provider_name, resolution_reason)
        """
        role_str = role.value if isinstance(role, ModelRole) else str(role)

        # 1. Adapt role based on TaskAnalysis if provided
        target_role = role_str
        reason = f"Explicit role '{role_str}'"

        if analysis is not None:
            complexity = getattr(analysis, "complexity", "simple")
            risk = getattr(analysis, "risk", "low")

            if (
                self.config.prefer_cheap_for_simple
                and complexity == "simple"
                and risk == "low"
                and role_str in (ModelRole.DEFAULT.value, "agent", "turn")
            ):
                target_role = ModelRole.CHEAP.value
                reason = "Task complexity=simple and risk=low -> routed to cheap provider"
            elif (
                (complexity == "complex" or risk in ("high", "critical"))
                and role_str in (ModelRole.DEFAULT.value, "agent", "turn")
            ):
                target_role = ModelRole.STRONG.value
                reason = f"Task complexity={complexity}, risk={risk} -> routed to strong provider"
            elif role_str in (ModelRole.DEFAULT.value, "agent", "turn"):
                target_role = role_str
                reason = f"Direct turn with complexity={complexity}, risk={risk}"

        # 2. Check role routes mapping
        mapped_name = self.config.role_routes.get(target_role)
        if mapped_name:
            if mapped_name in self._providers:
                return mapped_name, f"{reason} mapped to '{mapped_name}'"
            if mapped_name == "strong" and self.config.strong_provider in self._providers:
                return self.config.strong_provider, f"{reason} mapped to strong '{self.config.strong_provider}'"
            if mapped_name == "cheap" and self.config.cheap_provider in self._providers:
                return self.config.cheap_provider, f"{reason} mapped to cheap '{self.config.cheap_provider}'"

        # 3. Direct provider name matches target role
        if target_role in self._providers:
            return target_role, f"{reason} matched provider '{target_role}'"

        # 4. Alias checks
        if target_role == "strong" and self.config.strong_provider in self._providers:
            return self.config.strong_provider, f"{reason} matched strong_provider '{self.config.strong_provider}'"
        if target_role == "cheap" and self.config.cheap_provider in self._providers:
            return self.config.cheap_provider, f"{reason} matched cheap_provider '{self.config.cheap_provider}'"

        # 5. Fallback paths (guarantees offline single FakeProvider continues to work)
        if self.config.default_provider in self._providers:
            return self.config.default_provider, f"{reason} fallback to configured default '{self.config.default_provider}'"
        if "default" in self._providers:
            return "default", f"{reason} fallback to 'default' provider"
        if len(self._providers) == 1:
            sole_name = next(iter(self._providers))
            return sole_name, f"{reason} fallback to sole registered provider '{sole_name}'"
        if self._default_provider is not None:
            for k, v in self._providers.items():
                if v is self._default_provider:
                    return k, f"{reason} fallback to default_provider instance ('{k}')"
            return "default", f"{reason} fallback to default_provider instance"
        if "fake" in self._providers:
            return "fake", f"{reason} fallback to 'fake' provider"

        raise KeyError(
            f"No model provider found for role '{role_str}' (target '{target_role}'). "
            f"Registered providers: {list(self._providers.keys())}"
        )

    def route(
        self,
        role: ModelRole | str = ModelRole.DEFAULT,
        analysis: TaskAnalysis | None = None,
    ) -> tuple[ModelProvider, str, str]:
        """Route to provider instance, returning (provider, provider_name, reason).

        Records OpenTelemetry routing attributes on the active span.
        """
        provider_name, reason = self.resolve_provider_name(role, analysis)
        provider = self._providers.get(provider_name) or self._default_provider
        if provider is None:
            raise KeyError(f"Resolved provider name '{provider_name}' has no active instance.")

        role_str = role.value if isinstance(role, ModelRole) else str(role)
        record_routing_decision(
            role=role_str,
            provider_name=provider_name,
            reason=reason,
        )
        return provider, provider_name, reason

    def get_provider(
        self,
        role: ModelRole | str = ModelRole.DEFAULT,
        analysis: TaskAnalysis | None = None,
    ) -> ModelProvider:
        """Retrieve the provider instance for the given role and analysis."""
        provider, _, _ = self.route(role, analysis)
        return provider

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Execute completion turn delegating to default routed provider."""
        provider = self.get_provider(ModelRole.DEFAULT)
        return await provider.complete(request)


__all__ = [
    "ModelRole",
    "ModelRouter",
    "RoutingConfig",
]
