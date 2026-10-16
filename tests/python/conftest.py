"""Queue paths are enabled and isolated by default throughout Python tests."""

import pytest


@pytest.fixture(autouse=True)
def durable_queue_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("YUSUFCLAW_QUEUE_ENABLED", "1")
    monkeypatch.setenv("YUSUFCLAW_QUEUE_URL", f"sqlite+aiosqlite:///{tmp_path}/jobs.sqlite")
