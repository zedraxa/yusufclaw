"""Tests for FastAPI web gateway."""

import pytest
from httpx import ASGITransport, AsyncClient

from yusufclaw import __version__
from yusufclaw.config import Settings
from yusufclaw.gateway.web import create_app


@pytest.mark.asyncio
async def test_health_endpoint() -> None:
    """Verify /health returns 200 and valid JSON schema."""
    settings = Settings(model_provider="fake")
    app = create_app(settings)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == __version__
        assert data["service"] == "yusufclaw"
        assert data["model_provider"] == "fake"
