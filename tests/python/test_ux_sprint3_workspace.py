"""Tests for UX Sprint 3 workspace surfaces (UX-08, UX-11, UX-12, UX-13).

Coverage:
- Routines catalog create/list with next-due verification
- MCP connector hub pin / quarantine
- A2A teammates snapshot
- Usage meter $50 ceiling + meter.usage SSE
- Protocol version stamps (ag-ui/1.0, a2a/1.0, mcp/2025-03-26)
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from yusufclaw.a2a.hook import TeammateStatusHook
from yusufclaw.a2a.models import AgentCard, TeammateBudget
from yusufclaw.a2a.registry import TeammateRegistry
from yusufclaw.gateway.bus import MessageBus
from yusufclaw.gateway.web import create_app
from yusufclaw.gateway.web_page import CHAT_PAGE, CHAT_SCRIPT, CHAT_STYLE
from yusufclaw.gateway.workspace import (
    PROTOCOL_A2A,
    PROTOCOL_AG_UI,
    PROTOCOL_MCP,
    UsageMeter,
    next_cron_fire,
)
from yusufclaw.mcp.models import MCPConfig, MCPServerConfig
from yusufclaw.mcp.pinning import PinningStore
from yusufclaw.memory.sqlite import SqliteMemory


class SimpleAgent:
    async def handle(
        self,
        session: str,
        request_id: str,
        text: str,
        approve: Any = None,
        signal: Any = None,
        ask_question: Any = None,
    ) -> str:
        return f"echo:{text}"


@pytest.fixture
def memory_db() -> Any:
    mem = SqliteMemory(":memory:")
    yield mem
    mem.close()


def _app(
    memory: SqliteMemory,
    *,
    mcp: MCPConfig | None = None,
    pins: PinningStore | None = None,
    registry: TeammateRegistry | None = None,
    hook: TeammateStatusHook | None = None,
    meter: UsageMeter | None = None,
) -> Any:
    agent = SimpleAgent()
    bus = MessageBus(agent, web={"local"})
    return create_app(
        bus=bus,
        memory=memory,
        token="fixture-token",
        user_id="local",
        mcp_config=mcp,
        pinning_store=pins,
        teammate_registry=registry,
        teammate_hook=hook,
        usage_meter=meter or UsageMeter(memory=memory),
    )


AUTH = {"Authorization": "Bearer fixture-token"}


def test_next_cron_fire_returns_iso() -> None:
    due = next_cron_fire("0 8 * * *", after=__import__("datetime").datetime(2026, 10, 17, 7, 0, tzinfo=__import__("datetime").UTC))
    assert due is not None
    assert due.startswith("2026-10-17T08:00:00")


@pytest.mark.asyncio
async def test_ux08_routines_create_lists_next_due(memory_db: SqliteMemory) -> None:
    app = _app(memory_db)
    headers = {**AUTH, "Content-Type": "application/json"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        created = await client.post(
            "/api/automation/routines",
            headers=headers,
            json={
                "id": "morning-brief",
                "cron": "0 8 * * 1-5",
                "prompt": "Prepare morning briefing",
                "grants": ["web_fetch"],
                "misfirePolicy": "skip",
                "target": {"channel": "web", "userId": "local", "conversationId": "default"},
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["id"] == "morning-brief"
        assert body["nextDueAt"]
        assert body["grants"] == ["web_fetch"]
        assert body["protocolVersion"] == PROTOCOL_AG_UI

        listed = await client.get("/api/automation/routines", headers=AUTH)
        assert listed.status_code == 200
        data = listed.json()
        assert data["eventType"] == "routine.catalog"
        assert any(r["id"] == "morning-brief" for r in data["routines"])

        bad = await client.post(
            "/api/automation/routines",
            headers=headers,
            json={"id": "bad", "cron": "not-a-cron", "prompt": "x"},
        )
        assert bad.status_code == 400


@pytest.mark.asyncio
async def test_ux11_connectors_pin_and_quarantine(memory_db: SqliteMemory) -> None:
    mcp = MCPConfig(
        servers={
            "gmail": MCPServerConfig(
                name="gmail",
                transport="streamable_http",
                url="https://example.test/mcp",
                oauth_client_id="yc-gmail",
                oauth_redirect_uri="http://127.0.0.1:3000/oauth/callback",
                pinned_hash="",
            )
        }
    )
    pins = PinningStore()
    app = _app(memory_db, mcp=mcp, pins=pins)
    headers = {**AUTH, "Content-Type": "application/json"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        listed = await client.get("/api/connectors", headers=AUTH)
        assert listed.status_code == 200
        data = listed.json()
        assert data["protocolVersion"] == PROTOCOL_MCP
        assert data["connectors"][0]["status"] == "error"

        pinned = await client.post(
            "/api/connectors/gmail/pin",
            headers=headers,
            json={"toolsHash": "a" * 64},
        )
        assert pinned.status_code == 200
        assert pinned.json()["status"] == "connected"
        assert pins.get_record("gmail") is not None
        assert pins.get_record("gmail").status == "active"  # type: ignore[union-attr]

        quarantined = await client.post(
            "/api/connectors/gmail/quarantine",
            headers=headers,
            json={"reason": "hash drift"},
        )
        assert quarantined.status_code == 200
        assert quarantined.json()["status"] == "quarantined"


@pytest.mark.asyncio
async def test_ux12_teammates_snapshot_protocol(memory_db: SqliteMemory) -> None:
    registry = TeammateRegistry()
    registry.register(
        AgentCard(
            id="teammate_researcher_1",
            role="researcher",
            name="Researcher",
            capabilities=["web_fetch"],
            budget=TeammateBudget(max_cost_usd=0.05),
        )
    )
    hook = TeammateStatusHook()
    await hook.emit_spawned(
        subagent_id="teammate_researcher_1",
        parent_run_id="run-1",
        role="researcher",
        task="scan papers",
        tools=["web_fetch"],
        max_cost=0.05,
    )
    app = _app(memory_db, registry=registry, hook=hook)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        res = await client.get("/api/teammates", headers=AUTH)
    assert res.status_code == 200
    data = res.json()
    assert data["protocolVersion"] == PROTOCOL_A2A
    assert data["maxDepth"] == 1
    assert data["cards"][0]["protocolVersion"] == PROTOCOL_A2A
    assert data["recentEvents"]


@pytest.mark.asyncio
async def test_ux13_usage_meter_and_sse_event(memory_db: SqliteMemory) -> None:
    meter = UsageMeter(memory=memory_db, monthly_limit_usd=50.0)
    meter.monthly_spend_usd = 40.0
    app = _app(memory_db, meter=meter)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        usage = await client.get("/api/usage", headers=AUTH)
        assert usage.status_code == 200
        snap = usage.json()
        assert snap["monthlyLimitUsd"] == 50.0
        assert snap["budgetLevel"] == "yellow"
        assert snap["protocolVersion"] == PROTOCOL_AG_UI

        stream = await client.post(
            "/api/chat/stream",
            headers={**AUTH, "Content-Type": "application/json"},
            json={"text": "hello meter", "requestId": "req-meter", "conversationId": "c1"},
        )
        assert stream.status_code == 200
        body = stream.text
        assert "meter.usage" in body
        assert "protocolVersion" in body
        # Parse last meter event
        found = False
        for block in body.split("\n\n"):
            if "event: meter.usage" in block:
                data_line = [ln for ln in block.split("\n") if ln.startswith("data: ")][0]
                payload = json.loads(data_line[6:])
                assert payload.get("protocolVersion") == PROTOCOL_AG_UI
                found = True
        assert found


def test_ux_sprint3_ui_surfaces_present() -> None:
    assert 'id="tab-routines"' in CHAT_PAGE
    assert 'id="tab-connectors"' in CHAT_PAGE
    assert 'id="tab-teammates"' in CHAT_PAGE
    assert 'id="usage-meter"' in CHAT_PAGE
    assert "AG-UI 1.0 · A2A 1.0 · MCP 2025-03-26" in CHAT_PAGE
    assert "loadRoutines" in CHAT_SCRIPT
    assert "loadConnectors" in CHAT_SCRIPT
    assert "loadTeammates" in CHAT_SCRIPT
    assert "updateUsageMeter" in CHAT_SCRIPT
    assert "usage-bar-fill" in CHAT_STYLE
    assert "innerHTML" not in CHAT_SCRIPT
