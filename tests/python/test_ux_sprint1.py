"""Tests for UX-SPEC Sprint 1 backlog items UX-01 through UX-06.

Coverage:
- UX-01: AG-UI 1.0 protocol conformance gaps (typed events & envelopes, backward compatibility)
- UX-02: Named agents sidebar & session router (GET /api/agents, web:userId:agentId:conversationId isolation)
- UX-03: Immediate cognitive ack (<300ms) & milestone stepper events
- UX-04: Inline decision/question widgets (widget.question & POST /api/actions/resolve-widget)
- UX-05: Verifiable action/approval cards with Telegram cross-channel parity & tamper protection
- UX-06: Masked secret-request modal with local credential broker (zero leak in context/SQLite/logs)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from yusufclaw.agent import get_turn_event_hook
from yusufclaw.gateway.approval import (
    ActionCard,
    ActionCardRendered,
    InMemoryApprovalProvider,
)
from yusufclaw.gateway.bus import MessageBus
from yusufclaw.gateway.credentials import (
    CredentialBroker,
    get_credential_broker,
)
from yusufclaw.gateway.models import (
    AgUiEnvelope,
    MilestoneStep,
    QuestionOption,
    QuestionWidget,
    SecretRequestWidget,
)
from yusufclaw.gateway.web import create_app
from yusufclaw.memory.sqlite import SqliteMemory
from yusufclaw.models.types import ToolCall


def parse_sse_events(raw_sse: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse raw SSE text into a list of (event_name, data_dict) tuples."""
    events: list[tuple[str, dict[str, Any]]] = []
    blocks = raw_sse.strip().split("\n\n")
    for block in blocks:
        lines = block.strip().split("\n")
        event_name = ""
        data_str = ""
        for line in lines:
            if line.startswith("event: "):
                event_name = line[7:].strip()
            elif line.startswith("data: "):
                data_str = line[6:].strip()
        if event_name and data_str:
            try:
                events.append((event_name, json.loads(data_str)))
            except Exception:
                pass
    return events


class SimpleAgent:
    """Agent that returns simple text reply."""

    def __init__(self, reply: str = "Hello from agent") -> None:
        self.reply = reply
        self.recorded_sessions: list[str] = []

    async def handle(
        self,
        session: str,
        request_id: str,
        text: str,
        approve: Any = None,
        signal: Any = None,
        ask_question: Any = None,
    ) -> str:
        self.recorded_sessions.append(session)
        return self.reply


@pytest.fixture
def memory_db() -> Any:
    mem = SqliteMemory(":memory:")
    yield mem
    mem.close()


@pytest.fixture
def clean_broker() -> Any:
    broker = get_credential_broker()
    broker.clear()
    yield broker
    broker.clear()


@pytest.mark.asyncio
async def test_ux01_agui_protocol_conformance_and_envelope_schema(
    memory_db: SqliteMemory,
) -> None:
    """UX-01: Verifies SSE stream emits typed AG-UI 1.0 events with envelopes and legacy events."""
    agent = SimpleAgent("Protocol test passed")
    bus = MessageBus(agent, web={"local"})
    app = create_app(
        bus=bus,
        memory=memory_db,
        token="fixture-token",
        user_id="local",
    )

    headers = {
        "Authorization": "Bearer fixture-token",
        "Content-Type": "application/json",
    }
    payload = {
        "text": "run check",
        "requestId": "req-ux01-1",
        "conversationId": "conv-ux01-1",
        "agentId": "coder",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:3000") as client:
        res = await client.post("/api/chat/stream", json=payload, headers=headers)
        assert res.status_code == 200
        assert "text/event-stream" in res.headers["content-type"]

        events = parse_sse_events(res.text)
        event_names = [e[0] for e in events]

        # 1. AG-UI 1.0 typed events are present
        assert "run.acknowledged" in event_names
        assert "run.milestone" in event_names
        assert "message.delta" in event_names
        assert "run.completed" in event_names

        # 2. Legacy events are preserved for backward compatibility
        assert "RunStarted" in event_names
        assert "progress" in event_names
        assert "TextMessageStart" in event_names
        assert "TextMessageContent" in event_names
        assert "TextMessageEnd" in event_names
        assert "RunFinished" in event_names

        # 3. Envelope validation on run.acknowledged
        ack_event = next(data for name, data in events if name == "run.acknowledged")
        envelope = AgUiEnvelope.model_validate(ack_event)
        assert envelope.event_type == "run.acknowledged"
        assert "conv-ux01-1" in envelope.session_id
        assert envelope.agent_id == "coder"
        assert envelope.payload["intent"] == "run check"

        # 4. Envelope validation on run.milestone
        milestone_event = next(data for name, data in events if name == "run.milestone")
        m_env = AgUiEnvelope.model_validate(milestone_event)
        assert m_env.event_type == "run.milestone"
        milestone = MilestoneStep.model_validate(m_env.payload)
        assert milestone.title in ("Analyzing task requirements", "Initializing agent turn")

        # 5. Envelope validation on run.completed
        completed_event = next(data for name, data in events if name == "run.completed")
        c_env = AgUiEnvelope.model_validate(completed_event)
        assert c_env.event_type == "run.completed"
        assert c_env.payload["status"] in ("succeeded", "completed")


@pytest.mark.asyncio
async def test_ux02_named_agents_sidebar_and_session_router(
    memory_db: SqliteMemory,
) -> None:
    """UX-02: Verifies /api/agents catalog endpoint and session router isolation web:userId:agentId:convId."""
    agent = SimpleAgent("Agent routing response")
    bus = MessageBus(agent, web={"local"})
    app = create_app(
        bus=bus,
        memory=memory_db,
        token="fixture-token",
        user_id="local",
    )

    headers = {
        "Authorization": "Bearer fixture-token",
        "Content-Type": "application/json",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:3000") as client:
        # 1. GET /api/agents returns catalog personas
        res_agents = await client.get("/api/agents", headers=headers)
        assert res_agents.status_code == 200
        agents_list = res_agents.json()
        assert isinstance(agents_list, list)
        assert len(agents_list) > 0

        agent_ids = [a["agentId"] for a in agents_list]
        assert "orchestrator" in agent_ids
        assert "coder" in agent_ids
        assert "researcher" in agent_ids

        coder = next(a for a in agents_list if a["agentId"] == "coder")
        assert "name" in coder
        assert "description" in coder
        assert "model" in coder
        assert "status" in coder

        # 2. Session router: sending message to 'coder' agent
        payload_coder = {
            "text": "code this feature",
            "requestId": "req-coder-1",
            "conversationId": "conv-shared-42",
            "agentId": "coder",
        }
        res_coder = await client.post("/api/chat", json=payload_coder, headers=headers)
        assert res_coder.status_code == 200

        # 3. Session router: sending message to 'researcher' agent with same conversationId
        payload_researcher = {
            "text": "research this topic",
            "requestId": "req-res-1",
            "conversationId": "conv-shared-42",
            "agentId": "researcher",
        }
        res_res = await client.post("/api/chat", json=payload_researcher, headers=headers)
        assert res_res.status_code == 200

        # Verify session isolation in recorded sessions:
        assert len(agent.recorded_sessions) == 2
        assert "coder:conv-shared-42" in agent.recorded_sessions[0]
        assert "researcher:conv-shared-42" in agent.recorded_sessions[1]
        assert agent.recorded_sessions[0] != agent.recorded_sessions[1]


@pytest.mark.asyncio
async def test_ux03_immediate_cognitive_ack_and_milestones(
    memory_db: SqliteMemory,
) -> None:
    """UX-03: Immediate ack and milestone stepper events fire before turn completion."""

    class SlowAgent:
        async def handle(
            self,
            session: str,
            request_id: str,
            text: str,
            approve: Any = None,
            signal: Any = None,
            ask_question: Any = None,
        ) -> str:
            await asyncio.sleep(0.05)
            return "Slow execution completed"

    bus = MessageBus(SlowAgent(), web={"local"})
    app = create_app(
        bus=bus,
        memory=memory_db,
        token="fixture-token",
        user_id="local",
    )

    headers = {
        "Authorization": "Bearer fixture-token",
        "Content-Type": "application/json",
    }
    payload = {
        "text": "start long operation",
        "requestId": "req-slow-1",
        "conversationId": "conv-slow-1",
        "agentId": "coder",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:3000") as client:
        res = await client.post("/api/chat/stream", json=payload, headers=headers)
        assert res.status_code == 200
        events = parse_sse_events(res.text)

        event_names = [e[0] for e in events]
        ack_idx = event_names.index("run.acknowledged")
        milestone_idx = event_names.index("run.milestone")
        completed_idx = event_names.index("run.completed")

        # Ack and initial milestone are dispatched before run completes
        assert ack_idx < completed_idx
        assert milestone_idx < completed_idx

        # Milestone step models
        milestones = [data for name, data in events if name == "run.milestone"]
        assert len(milestones) >= 2
        assert milestones[0]["payload"]["status"] == "running"
        assert milestones[-1]["payload"]["status"] == "completed"


@pytest.mark.asyncio
async def test_ux04_inline_question_widget_resolution_lifecycle(
    memory_db: SqliteMemory,
) -> None:
    """UX-04: Inline question widget is emitted and resolved via POST /api/actions/resolve-widget."""

    class QuestionDemandingAgent:
        async def handle(
            self,
            session: str,
            request_id: str,
            text: str,
            approve: Any = None,
            signal: Any = None,
            ask_question: Any = None,
        ) -> str:
            assert ask_question is not None
            widget = QuestionWidget(
                questionId="q-deploy-env",
                prompt="Select target deployment environment:",
                options=[
                    QuestionOption(id="staging", label="Staging", description="Safe staging cluster"),
                    QuestionOption(id="production", label="Production", description="Live cluster"),
                ],
                isMultiSelect=False,
            )
            selected = await ask_question(widget)
            return f"Deployment selected: {selected}"

    bus = MessageBus(QuestionDemandingAgent(), web={"local"})
    app = create_app(
        bus=bus,
        memory=memory_db,
        token="fixture-token",
        user_id="local",
    )

    headers = {
        "Authorization": "Bearer fixture-token",
        "Content-Type": "application/json",
    }
    payload = {
        "text": "deploy project",
        "requestId": "req-q-1",
        "conversationId": "conv-q-1",
        "agentId": "ops",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:3000") as client:
        stream_task = asyncio.create_task(
            client.post("/api/chat/stream", json=payload, headers=headers)
        )

        # Allow SSE stream to start and emit question widget
        await asyncio.sleep(0.05)

        # 1. Invalid / unknown questionId -> 404 or 400
        bad_res = await client.post(
            "/api/actions/resolve-widget",
            json={"questionId": "unknown-q", "selectedOptionIds": ["staging"]},
            headers=headers,
        )
        assert bad_res.status_code in (400, 404)

        # 2. Missing questionId or invalid format -> 400
        empty_res = await client.post(
            "/api/actions/resolve-widget",
            json={"selectedOptionIds": ["staging"]},
            headers=headers,
        )
        assert empty_res.status_code == 400

        # 3. Valid resolution
        resolve_res = await client.post(
            "/api/actions/resolve-widget",
            json={"questionId": "q-deploy-env", "selectedOptionIds": ["staging"]},
            headers=headers,
        )
        assert resolve_res.status_code == 200
        assert resolve_res.json() == {
            "status": "resolved",
            "questionId": "q-deploy-env",
            "selectedOptionIds": ["staging"],
            "customText": None,
        }

        # 4. Duplicate submission on already resolved question -> 400
        dup_res = await client.post(
            "/api/actions/resolve-widget",
            json={"questionId": "q-deploy-env", "selectedOptionIds": ["production"]},
            headers=headers,
        )
        assert dup_res.status_code == 400
        assert "already resolved" in dup_res.json()["error"]

        # Wait for stream to finish
        stream_res = await stream_task
        assert stream_res.status_code == 200
        events = parse_sse_events(stream_res.text)

        event_names = [e[0] for e in events]
        assert "widget.question" in event_names
        q_event = next(data for name, data in events if name == "widget.question")
        assert q_event["payload"]["questionId"] == "q-deploy-env"
        assert len(q_event["payload"]["options"]) == 2

        # Verify final message reflects resolved decision
        text_end = next(data for name, data in events if name == "TextMessageEnd")
        assert "staging" in text_end["text"]


@pytest.mark.asyncio
async def test_ux05_action_card_approval_editing_and_cross_channel_parity(
    memory_db: SqliteMemory,
) -> None:
    """UX-05: ActionCard support with anti-tampering protection and Telegram cross-channel parity."""
    approval_provider = InMemoryApprovalProvider()
    original_args = {"path": "/etc/config.json", "content": "enabled=true"}

    class FileWriteAgent:
        async def handle(
            self,
            session: str,
            request_id: str,
            text: str,
            approve: Any = None,
            signal: Any = None,
            ask_question: Any = None,
        ) -> str:
            assert approve is not None
            card = ActionCard(
                approvalId=f"app-card-{request_id}",
                nonce=f"nonce-{request_id}",
                toolId="write_file",
                rendered=ActionCardRendered(
                    title="Write Configuration File",
                    exact=json.dumps(original_args),
                    effects=["filesystem:write"],
                    taint="clean",
                    reversible=True,
                ),
            )
            tc = ToolCall(id=f"call-{request_id}", name="write_file", input=original_args)
            approved = await approve(tc, card)
            if approved:
                return f"Wrote configuration successfully with args: {tc.input}"
            return "Configuration write was denied."

    bus = MessageBus(FileWriteAgent(), web={"local"})
    app = create_app(
        bus=bus,
        memory=memory_db,
        token="fixture-token",
        user_id="local",
        approval_provider=approval_provider,
    )

    headers = {
        "Authorization": "Bearer fixture-token",
        "Content-Type": "application/json",
    }
    payload_a = {
        "text": "update config file",
        "requestId": "req-ux05-1",
        "conversationId": "conv-ux05-1",
        "agentId": "coder",
    }

    # Scenario A: Anti-tampering check and valid approval
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:3000") as client:
        stream_task = asyncio.create_task(
            client.post("/api/chat/stream", json=payload_a, headers=headers)
        )
        await asyncio.sleep(0.05)

        # 1. Tampering check: supplying altered args fails closed
        res_tamper = await client.post(
            "/api/actions/resolve-approval",
            json={
                "approvalId": "app-card-req-ux05-1",
                "nonce": "nonce-req-ux05-1",
                "decision": "approve",
                "args": {"path": "/etc/shadow", "content": "tampered"},
            },
            headers=headers,
        )
        assert res_tamper.status_code == 400
        assert "tampering" in res_tamper.json()["error"]

        # 2. Legitimate approval with matching original args
        res_ok = await client.post(
            "/api/actions/resolve-approval",
            json={
                "approvalId": "app-card-req-ux05-1",
                "nonce": "nonce-req-ux05-1",
                "decision": "approve",
                "args": original_args,
            },
            headers=headers,
        )
        assert res_ok.status_code == 200
        assert res_ok.json()["status"] == "approved"

        stream_res = await stream_task
        assert stream_res.status_code == 200
        events = parse_sse_events(stream_res.text)

        event_names = [e[0] for e in events]
        assert "widget.approval_card" in event_names
        app_event = next(data for name, data in events if name == "widget.approval_card")
        assert app_event["payload"]["approvalId"] == "app-card-req-ux05-1"
        assert app_event["payload"]["rendered"]["title"] == "Write Configuration File"
        assert app_event["payload"]["rendered"]["effects"] == ["filesystem:write"]
        assert app_event["payload"]["rendered"]["reversible"] is True

        text_end = next(data for name, data in events if name == "TextMessageEnd")
        assert "Wrote configuration successfully" in text_end["text"]

    # Scenario B: Cross-channel parity (external approval by Telegram / CLI provider)
    payload_b = {
        "text": "update config file",
        "requestId": "req-ux05-2",
        "conversationId": "conv-ux05-2",
        "agentId": "coder",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:3000") as client:
        stream_task_b = asyncio.create_task(
            client.post("/api/chat/stream", json=payload_b, headers=headers)
        )
        await asyncio.sleep(0.05)

        # External channel consumes the approval via provider
        consume_result = approval_provider.consume(
            approval_id="app-card-req-ux05-2",
            nonce="nonce-req-ux05-2",
            actual_args=original_args,
        )
        assert consume_result.success

        # Web watcher detects external consumption and unblocks stream
        stream_res_b = await asyncio.wait_for(stream_task_b, timeout=2.0)
        assert stream_res_b.status_code == 200
        events_b = parse_sse_events(stream_res_b.text)
        text_end_b = next(data for name, data in events_b if name == "TextMessageEnd")
        assert "Wrote configuration successfully" in text_end_b["text"]

    # Scenario C: Denial decision
    payload_c = {
        "text": "update config file",
        "requestId": "req-ux05-3",
        "conversationId": "conv-ux05-3",
        "agentId": "coder",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:3000") as client:
        stream_task_c = asyncio.create_task(
            client.post("/api/chat/stream", json=payload_c, headers=headers)
        )
        await asyncio.sleep(0.05)

        res_deny = await client.post(
            "/api/actions/resolve-approval",
            json={
                "approvalId": "app-card-req-ux05-3",
                "nonce": "nonce-req-ux05-3",
                "decision": "deny",
            },
            headers=headers,
        )
        assert res_deny.status_code == 200
        assert res_deny.json()["status"] == "denied"

        stream_res_c = await asyncio.wait_for(stream_task_c, timeout=2.0)
        assert stream_res_c.status_code == 200
        events_c = parse_sse_events(stream_res_c.text)
        text_end_c = next(data for name, data in events_c if name == "TextMessageEnd")
        assert "denied" in text_end_c["text"]


@pytest.mark.asyncio
async def test_ux06_masked_secret_request_and_local_credential_broker(
    memory_db: SqliteMemory,
    clean_broker: CredentialBroker,
) -> None:
    """UX-06: CredentialBroker stores secrets locally; plaintext is never exposed in responses or DB."""
    broker = clean_broker

    # 1. Unit verification of CredentialBroker
    fp = broker.store(
        key_name="github_pat",
        secret_value="ghp_SECRET_TOKEN_XYZ_12345",
        service_name="github",
        description="GitHub Release API",
    )
    assert len(fp) == 12

    meta = broker.get_metadata("github_pat")
    assert meta is not None
    assert meta.fingerprint == fp
    assert meta.key_name == "github_pat"
    # Verify raw secret not exposed in metadata
    meta_dict = meta.model_dump()
    assert "ghp_SECRET_TOKEN_XYZ_12345" not in str(meta_dict)

    # Context injection pill check
    pill = CredentialBroker.format_system_pill("github_pat", fp)
    assert "github_pat" in pill
    assert fp in pill
    assert "ghp_SECRET_TOKEN_XYZ_12345" not in pill

    # Backend retrieval
    retrieved = broker.get("github_pat")
    assert retrieved == "ghp_SECRET_TOKEN_XYZ_12345"

    class SecretDemandingAgent:
        async def handle(
            self,
            session: str,
            request_id: str,
            text: str,
            approve: Any = None,
            signal: Any = None,
            ask_question: Any = None,
        ) -> str:
            # Emit secret request widget
            hook = get_turn_event_hook()
            if hook:
                widget = SecretRequestWidget(
                    requestId="sec-req-gh-token",
                    keyName="github_token",
                    serviceName="github",
                    description="Push release tags",
                )
                await hook(
                    "widget.secret_request",
                    widget.model_dump(by_alias=True),
                )
            return "Secret workflow initiated"

    bus = MessageBus(SecretDemandingAgent(), web={"local"})
    app = create_app(
        bus=bus,
        memory=memory_db,
        token="fixture-token",
        user_id="local",
    )

    headers = {
        "Authorization": "Bearer fixture-token",
        "Content-Type": "application/json",
    }
    payload = {
        "text": "need github auth",
        "requestId": "req-sec-1",
        "conversationId": "conv-sec-1",
        "agentId": "coder",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:3000") as client:
        # Start SSE stream in background task so interactive secret submission can resolve it
        stream_task = asyncio.create_task(
            client.post("/api/chat/stream", json=payload, headers=headers)
        )
        await asyncio.sleep(0.05)

        # 2. Submit secret via POST /api/actions/secret-submit
        raw_secret_value = "ghp_SuperSecret999888777"
        res_submit = await client.post(
            "/api/actions/secret-submit",
            json={
                "requestId": "sec-req-gh-token",
                "keyName": "github_token",
                "value": raw_secret_value,
                "serviceName": "github",
            },
            headers=headers,
        )
        assert res_submit.status_code == 200
        submit_data = res_submit.json()
        assert submit_data["status"] == "stored"
        assert submit_data["keyName"] == "github_token"
        assert submit_data["fingerprint"] is not None
        # Ensure plaintext secret is NOT returned in response
        assert raw_secret_value not in json.dumps(submit_data)

        # Stream completes rapidly
        stream_res = await asyncio.wait_for(stream_task, timeout=2.0)
        assert stream_res.status_code == 200
        events = parse_sse_events(stream_res.text)

        event_names = [e[0] for e in events]
        assert "widget.secret_request" in event_names
        sec_event = next(data for name, data in events if name == "widget.secret_request")
        assert sec_event["payload"]["keyName"] == "github_token"
        assert sec_event["payload"]["serviceName"] == "github"

        # 3. GET /api/credentials lists metadata only
        res_creds = await client.get("/api/credentials", headers=headers)
        assert res_creds.status_code == 200
        creds_list = res_creds.json()
        assert isinstance(creds_list, list)
        assert any(c["keyName"] == "github_token" for c in creds_list)
        # Ensure raw secret is NOT present in metadata listing
        assert raw_secret_value not in json.dumps(creds_list)

        # 4. Verify SQLite memory DB does NOT contain raw secret in any table/turn
        all_turns = memory_db._conn.execute("SELECT * FROM turns").fetchall()
        for turn_row in all_turns:
            assert raw_secret_value not in str(dict(turn_row))

        all_records = memory_db._conn.execute("SELECT * FROM memory_records").fetchall()
        for rec_row in all_records:
            assert raw_secret_value not in str(dict(rec_row))
