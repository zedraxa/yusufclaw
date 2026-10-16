"""FastAPI web gateway for YusufClaw.

Provides HTTP REST endpoints and event streaming interfaces:
- GET /health: Health probe for load balancers and container orchestrators.
- Chat session endpoints and streaming server-sent events (AG-UI protocol).
"""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from yusufclaw import __version__
from yusufclaw.config import Settings, get_settings
from yusufclaw.telemetry import setup_telemetry


class HealthResponse(BaseModel):
    """Health check response schema."""

    status: str
    version: str
    service: str
    model_provider: str


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI web gateway application."""
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        setup_telemetry(
            service_name=cfg.otel_service_name,
            otlp_endpoint=cfg.otel_exporter_otlp_endpoint,
        )
        yield

    app = FastAPI(
        title="YusufClaw API",
        version=__version__,
        description="Autonomous personal AI agent gateway API",
        lifespan=lifespan,
    )

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Health check endpoint returning system status."""
        return HealthResponse(
            status="ok",
            version=__version__,
            service="yusufclaw",
            model_provider=cfg.model_provider,
        )

    return app


app = create_app()

__all__ = ["HealthResponse", "app", "create_app"]
