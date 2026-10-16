import pytest

import yusufclaw.a2a  # noqa: F401


@pytest.fixture(autouse=True)
def durable_queue_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
    monkeypatch.setenv("YUSUFCLAW_QUEUE_ENABLED", "1")
    monkeypatch.setenv("YUSUFCLAW_QUEUE_URL", f"sqlite+aiosqlite:///{tmp_path}/jobs.sqlite")
