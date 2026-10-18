"""Tests for UX-SPEC Sprint 2 backlog item UX-07.

Coverage:
- Transparent memory inspection canvas (GET /api/memory)
- Deterministic forget / unlearning (POST /api/memory/forget)
- In-place revise with CAS (PATCH /api/memory/{id})
- Web UI includes Memory tab, split panes, and forget modal (CSP-safe)
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from yusufclaw.gateway.bus import MessageBus
from yusufclaw.gateway.web import create_app
from yusufclaw.gateway.web_page import CHAT_PAGE, CHAT_SCRIPT, CHAT_STYLE
from yusufclaw.memory.models import RememberInput
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
        return "ok"


@pytest.fixture
def memory_db() -> Any:
    mem = SqliteMemory(":memory:")
    yield mem
    mem.close()


def _seed_scope(mem: SqliteMemory, scope: str) -> dict[str, str]:
    profile = mem.remember(
        scope,
        RememberInput(
            text="User.location = Kadıköy, İstanbul",
            kind="profile",
            trust="user_explicit",
            category="location",
        ),
    )
    fact = mem.remember(
        scope,
        RememberInput(
            text="Prefers Haiku for routine tasks",
            kind="fact",
            trust="observed",
            category="preference",
        ),
    )
    note = mem.remember(
        scope,
        RememberInput(
            text="Discussed UX-07 memory canvas acceptance gates",
            kind="note",
            trust="model_inferred",
            category="journal",
        ),
    )
    return {"profile": profile.id, "fact": fact.id, "note": note.id}


@pytest.mark.asyncio
async def test_ux07_memory_canvas_lists_profile_facts_and_journal(
    memory_db: SqliteMemory,
) -> None:
    scope = "web:local:orchestrator:default"
    ids = _seed_scope(memory_db, scope)
    # Isolation: other session must not appear.
    memory_db.remember(
        "web:local:coder:other",
        RememberInput(text="secret other scope", kind="fact"),
    )

    agent = SimpleAgent()
    bus = MessageBus(agent, web={"local"})
    app = create_app(bus=bus, memory=memory_db, token="fixture-token", user_id="local")
    headers = {"Authorization": "Bearer fixture-token"}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        res = await client.get(
            "/api/memory",
            params={"agentId": "orchestrator", "conversationId": "default"},
            headers=headers,
        )
    assert res.status_code == 200
    data = res.json()
    assert data["scope"] == scope
    assert data["eventType"] == "memory.canvas"
    assert {r["id"] for r in data["profile"]} == {ids["profile"]}
    assert {r["id"] for r in data["facts"]} == {ids["fact"]}
    assert {r["id"] for r in data["journal"]} == {ids["note"]}
    assert all("embedding" not in r for r in data["profile"] + data["facts"] + data["journal"])
    leaked = [
        r
        for r in data["profile"] + data["facts"] + data["journal"]
        if "secret other scope" in r.get("text", "")
    ]
    assert leaked == []


@pytest.mark.asyncio
async def test_ux07_forget_tombstones_and_removes_from_active_retrieval(
    memory_db: SqliteMemory,
) -> None:
    scope = "web:local:researcher:conv-1"
    ids = _seed_scope(memory_db, scope)
    agent = SimpleAgent()
    bus = MessageBus(agent, web={"local"})
    app = create_app(bus=bus, memory=memory_db, token="fixture-token", user_id="local")
    headers = {
        "Authorization": "Bearer fixture-token",
        "Content-Type": "application/json",
    }

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        res = await client.post(
            "/api/memory/forget",
            headers=headers,
            json={
                "recordId": ids["fact"],
                "agentId": "researcher",
                "conversationId": "conv-1",
                "reason": "user requested forget",
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["eventType"] == "memory.record_deleted"
        assert body["recordId"] == ids["fact"]
        assert body["scope"] == scope
        assert "FTS" in body["message"]

        # Duplicate forget fails closed.
        again = await client.post(
            "/api/memory/forget",
            headers=headers,
            json={
                "recordId": ids["fact"],
                "agentId": "researcher",
                "conversationId": "conv-1",
            },
        )
        assert again.status_code == 404

        listed = await client.get(
            "/api/memory",
            params={"agentId": "researcher", "conversationId": "conv-1"},
            headers={"Authorization": "Bearer fixture-token"},
        )
    assert listed.status_code == 200
    data = listed.json()
    assert ids["fact"] not in {r["id"] for r in data["facts"]}
    assert ids["profile"] in {r["id"] for r in data["profile"]}

    # Tombstone + FTS purge at store layer.
    gone = memory_db.get(scope, ids["fact"])
    assert gone is None
    inactive = memory_db.get(scope, ids["fact"], include_inactive=True)
    assert inactive is not None
    assert inactive.status == "deleted"
    hits = memory_db.search(scope, "Haiku")
    assert all(h.id != ids["fact"] for h in hits)


@pytest.mark.asyncio
async def test_ux07_revise_updates_text_and_rejects_stale_revision(
    memory_db: SqliteMemory,
) -> None:
    scope = "web:local:coder:default"
    ids = _seed_scope(memory_db, scope)
    agent = SimpleAgent()
    bus = MessageBus(agent, web={"local"})
    app = create_app(bus=bus, memory=memory_db, token="fixture-token", user_id="local")
    headers = {
        "Authorization": "Bearer fixture-token",
        "Content-Type": "application/json",
    }

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        ok = await client.patch(
            f"/api/memory/{ids['profile']}",
            headers=headers,
            json={
                "text": "User.location = Beşiktaş, İstanbul",
                "agentId": "coder",
                "conversationId": "default",
                "expectedRevision": 1,
            },
        )
        assert ok.status_code == 200
        payload = ok.json()
        assert payload["eventType"] == "memory.record_updated"
        assert payload["previousRevision"] == 1
        assert payload["record"]["revision"] == 2
        assert "Beşiktaş" in payload["record"]["text"]

        stale = await client.patch(
            f"/api/memory/{ids['profile']}",
            headers=headers,
            json={
                "text": "stale write",
                "agentId": "coder",
                "conversationId": "default",
                "expectedRevision": 1,
            },
        )
        assert stale.status_code == 409


def test_ux07_web_ui_contains_memory_canvas_and_csp_safe_patterns() -> None:
    assert 'id="tab-memory"' in CHAT_PAGE
    assert 'id="memory-view"' in CHAT_PAGE
    assert 'id="memory-profile"' in CHAT_PAGE
    assert 'id="memory-journal"' in CHAT_PAGE
    assert 'id="forget-modal"' in CHAT_PAGE
    assert "memory-split" in CHAT_STYLE
    assert "loadMemoryCanvas" in CHAT_SCRIPT
    assert "confirmForget" in CHAT_SCRIPT
    assert "/api/memory/forget" in CHAT_SCRIPT
    assert "innerHTML" not in CHAT_SCRIPT
    assert "localStorage" not in CHAT_SCRIPT
    assert "sessionStorage" not in CHAT_SCRIPT
