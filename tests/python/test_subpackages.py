"""Tests for YusufClaw subpackages docstrings and interfaces."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import yusufclaw.gateway as gateway
import yusufclaw.memory as memory
import yusufclaw.queue as queue_pkg
import yusufclaw.roles as roles
import yusufclaw.routines as routines
import yusufclaw.security as security
import yusufclaw.skills as skills
import yusufclaw.telemetry as telemetry
import yusufclaw.tools as tools
from yusufclaw.gateway.telegram import TelegramAdapter
from yusufclaw.queue.worker import RedisWakeup


def test_subpackages_docstrings() -> None:
    """Verify that all target subpackages have non-empty docstrings explaining their scope."""
    subpackages = [
        gateway,
        memory,
        roles,
        routines,
        security,
        skills,
        telemetry,
        tools,
        queue_pkg,
    ]
    for pkg in subpackages:
        assert pkg.__doc__ is not None, f"Package {pkg.__name__} has no docstring"
        assert len(pkg.__doc__.strip()) > 20, f"Package {pkg.__name__} docstring is too short"


def test_telemetry_setup() -> None:
    """Verify OpenTelemetry tracer and meter initialization."""
    telemetry.setup_telemetry(service_name="test-yusufclaw")
    tracer = telemetry.get_tracer("test")
    meter = telemetry.get_meter("test")
    assert tracer is not None
    assert meter is not None


@pytest.mark.asyncio
async def test_redis_wakeup_mocked() -> None:
    """Redis only holds disposable hints; outages must not lose jobs."""
    wakeup = RedisWakeup("redis://127.0.0.1:6379/1")
    await wakeup.close()
    mock = MagicMock()
    mock.lpush = AsyncMock(side_effect=ConnectionError())
    mock.blpop = AsyncMock(side_effect=ConnectionError())
    wakeup.client = mock
    await wakeup.notify()
    await wakeup.wait(0.01)


@pytest.mark.asyncio
async def test_telegram_adapter_placeholder() -> None:
    """Verify TelegramAdapter placeholder raises NotImplementedError on start."""
    adapter = TelegramAdapter(bot_token="test", allowed_users=["123"])
    with pytest.raises(NotImplementedError):
        await adapter.start()
    await adapter.stop()
