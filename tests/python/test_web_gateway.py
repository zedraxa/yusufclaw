import asyncio
import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from yusufclaw.agent import get_turn_event_hook
from yusufclaw.gateway.approval import (
    ActionCard,
    ActionCardRendered,
    InMemoryApprovalProvider,
)
from yusufclaw.gateway.bus import MessageBus
from yusufclaw.gateway.web import (
    ApprovalInput,
    RoutineTarget,
    WebAdapter,
    create_app,
    sanitize_args_summary,
    sanitize_secret_patterns,
)
from yusufclaw.memory.sqlite import SqliteMemory
from yusufclaw.models.types import ToolCall


class FakeAgent:
    def __init__(self, reply: str = "<script>hello</script>") -> None:
        self.reply = reply
        self.call_count = 0
        self.recorded_calls: list[dict[str, Any]] = []

    async def handle(
        self,
        session: str,
        request_id: str,
        text: str,
        approve: Any = None,
        signal: Any = None,
    ) -> str:
        self.call_count += 1
        self.recorded_calls.append(
            {"session": session, "request_id": request_id, "text": text}
        )
        return self.reply


@pytest.fixture
def memory_db() -> Any:
    mem = SqliteMemory(":memory:")
    yield mem
    mem.close()


@pytest.mark.asyncio
async def test_web_auth_cross_origin_validation_and_token_free_page(
    memory_db: SqliteMemory,
) -> None:
    """Verifies authentication, origin check, content-type and body size limits, and security headers."""
    agent = FakeAgent()
    bus = MessageBus(agent, web={"local"})
    app = create_app(
        bus=bus,
        memory=memory_db,
        token="fixture-token",
        user_id="local",
    )

    base_headers = {
        "Authorization": "Bearer fixture-token",
        "Content-Type": "application/json",
    }
    valid_message = {
        "text": "hello",
        "requestId": "1",
        "conversationId": "default",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:3000") as client:
        # 1. Missing and wrong auth token -> 401
        res_no_auth = await client.post("/api/chat", json=valid_message)
        assert res_no_auth.status_code == 401

        res_wrong_auth = await client.post(
            "/api/chat",
            json=valid_message,
            headers={"Authorization": "Bearer wrong-token", "Content-Type": "application/json"},
        )
        assert res_wrong_auth.status_code == 401

        # 2. Denies cross-origin requests -> 403
        res_origin = await client.post(
            "/api/chat",
            json=valid_message,
            headers={**base_headers, "Origin": "http://evil.example"},
        )
        assert res_origin.status_code == 403

        # 3. Content-Type must be application/json -> 415
        res_ct = await client.post(
            "/api/chat",
            content=b"text",
            headers={"Authorization": "Bearer fixture-token", "Content-Type": "text/plain"},
        )
        assert res_ct.status_code == 415

        # 4. Input validation (strict forbid extra fields, empty text, oversized text) -> 400
        for bad_body in [
            {**valid_message, "userId": "other"},  # unexpected field
            {**valid_message, "text": ""},  # empty
            {**valid_message, "text": "a" * 16001},  # too long
            {"text": "hi"},  # missing conversationId, requestId
        ]:
            res_bad = await client.post("/api/chat", json=bad_body, headers=base_headers)
            assert res_bad.status_code == 400

        # 5. Oversized payload > 100,000 bytes -> 413
        oversized = {**valid_message, "text": "a" * 100001}
        res_large = await client.post("/api/chat", json=oversized, headers=base_headers)
        assert res_large.status_code in (400, 413)

        assert agent.call_count == 0

        # 6. /api/routines requires auth
        res_routines_no_auth = await client.get("/api/routines")
        assert res_routines_no_auth.status_code == 401

        # 7. Web page serves token-free HTML with strict CSP
        res_page = await client.get("/")
        assert res_page.status_code == 200
        assert "fixture-token" not in res_page.text
        assert "frame-ancestors 'none'" in res_page.headers.get("content-security-policy", "")

        # 8. Script contains textContent and no unsafe storage/innerHTML
        res_js = await client.get("/app.js")
        assert res_js.status_code == 200
        assert "textContent" in res_js.text
        assert "innerHTML" not in res_js.text
        assert "localStorage" not in res_js.text
        assert "sessionStorage" not in res_js.text


@pytest.mark.asyncio
async def test_web_chat_success_and_error_sanitization(
    memory_db: SqliteMemory,
) -> None:
    """Verifies successful turn returns JSON reply and provider exceptions are masked to 502."""
    agent = FakeAgent(reply="<script>hello</script>")
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
    valid_message = {
        "text": "hello",
        "requestId": "req-1",
        "conversationId": "default",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:3000") as client:
        # Success response
        res = await client.post("/api/chat", json=valid_message, headers=headers)
        assert res.status_code == 200
        assert res.json() == {"reply": "<script>hello</script>"}
        assert agent.call_count == 1

        # Internal error throwing critical secret -> 502 with secret hidden
        class ThrowingAgent:
            async def handle(self, *args: Any, **kwargs: Any) -> str:
                raise RuntimeError("SUPER_SECRET_AWS_KEY")

        err_bus = MessageBus(ThrowingAgent(), web={"local"})
        err_app = create_app(bus=err_bus, memory=memory_db, token="fixture-token", user_id="local")

        async with AsyncClient(transport=ASGITransport(app=err_app), base_url="http://127.0.0.1:3000") as err_client:
            res_err = await err_client.post("/api/chat", json=valid_message, headers=headers)
            assert res_err.status_code == 502
            assert "SUPER_SECRET_AWS_KEY" not in res_err.text
            assert "Request failed" in res_err.json()["error"]


@pytest.mark.asyncio
async def test_web_scheduled_replies_delivery_and_inbox(
    memory_db: SqliteMemory,
) -> None:
    """Scheduled routine replies are delivered to user inbox and retrieved via /api/routines."""
    agent = FakeAgent()
    bus = MessageBus(agent, web={"local"})
    adapter = WebAdapter(
        bus=bus,
        memory=memory_db,
        options={"token": "fixture-token", "userId": "local", "port": 3000},
    )

    # Deliver to inbox
    target = RoutineTarget(channel="web", userId="local", conversationId="planning")
    await adapter.deliver(target, "<script>hello</script>", "routine:plan:1")
    # Duplicate delivery with same requestId is deduplicated
    await adapter.deliver(target, "<script>hello</script>", "routine:plan:1")

    # Target for different user is rejected
    with pytest.raises(ValueError, match="target"):
        await adapter.deliver(
            RoutineTarget(channel="web", userId="other", conversationId="planning"),
            "secret",
            "routine:plan:2",
        )

    # Fetch from API
    async with AsyncClient(transport=ASGITransport(app=adapter.app), base_url="http://127.0.0.1:3000") as client:
        res = await client.get("/api/routines", headers={"Authorization": "Bearer fixture-token"})
        assert res.status_code == 200
        items = res.json()
        assert len(items) == 1
        assert items[0] == {
            "requestId": "routine:plan:1",
            "conversationId": "planning",
            "reply": "<script>hello</script>",
        }


@pytest.mark.asyncio
async def test_web_adapter_requires_token_and_socket_lifecycle(
    memory_db: SqliteMemory,
) -> None:
    """WebAdapter validates non-empty token, binds ephemeral port, and shuts down cleanly."""
    agent = FakeAgent()
    bus = MessageBus(agent, web={"local"})

    # Empty token raises error
    with pytest.raises(ValueError, match="WEB_AUTH_TOKEN"):
        WebAdapter(bus, memory_db, {"token": "", "userId": "local"})

    adapter = WebAdapter(
        bus,
        memory_db,
        {"token": "test-token", "userId": "local", "port": 0},
    )
    await adapter.listen()
    addr = adapter.server.address()
    assert addr.address == "127.0.0.1"
    assert addr.port > 0
    assert adapter.server.listening is True

    await adapter.close()
    assert adapter.server.listening is False


@pytest.mark.asyncio
async def test_web_ag_ui_sse_streaming(memory_db: SqliteMemory) -> None:
    """Web gateway streams AG-UI events (progress, message, done) over SSE."""
    agent = FakeAgent(reply="SSE streamed response")
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
        "text": "run task",
        "requestId": "stream-1",
        "conversationId": "conv-stream",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:3000") as client:
        # 1. POST /api/chat/stream
        res = await client.post("/api/chat/stream", json=payload, headers=headers)
        assert res.status_code == 200
        assert "text/event-stream" in res.headers["content-type"]
        body = res.text
        assert "event: progress" in body
        assert "event: message" in body
        assert "SSE streamed response" in body
        assert "event: done" in body

        # 2. POST /api/chat with Accept: text/event-stream
        res_chat_sse = await client.post(
            "/api/chat",
            json=payload,
            headers={**headers, "Accept": "text/event-stream"},
        )
        assert res_chat_sse.status_code == 200
        assert "text/event-stream" in res_chat_sse.headers["content-type"]
        assert "event: message" in res_chat_sse.text

        # 3. GET /api/events
        res_events = await client.get(
            "/api/events",
            headers={"Authorization": "Bearer fixture-token"},
        )
        assert res_events.status_code == 200
        assert "text/event-stream" in res_events.headers["content-type"]
        assert "event: connected" in res_events.text


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
        if event_name:
            try:
                data = json.loads(data_str)
            except Exception:
                data = {"raw": data_str}
            events.append((event_name, data))
    return events


@pytest.mark.asyncio
async def test_web_ag_ui_1_0_run_lifecycle_and_fallback(memory_db: SqliteMemory) -> None:
    """AG-UI 1.0 stream emits full lifecycle (RunStarted, TextMessageStart/Content/End, RunFinished)."""
    agent = FakeAgent(reply="Full AG-UI 1.0 turn reply")
    bus = MessageBus(agent, web={"local"})
    app = create_app(bus=bus, memory=memory_db, token="fixture-token", user_id="local")

    headers = {
        "Authorization": "Bearer fixture-token",
        "Content-Type": "application/json",
    }
    payload = {
        "text": "execute command",
        "requestId": "req-agui-1",
        "conversationId": "conv-agui-1",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:3000") as client:
        res = await client.post("/api/chat/stream", json=payload, headers=headers)
        assert res.status_code == 200
        events = parse_sse_events(res.text)

        event_types = [e[0] for e in events]
        assert "RunStarted" in event_types
        assert "progress" in event_types
        assert "TextMessageStart" in event_types
        assert "TextMessageContent" in event_types
        assert "TextMessageEnd" in event_types
        assert "message" in event_types
        assert "done" in event_types
        assert "RunFinished" in event_types

        # Validate RunStarted payload
        run_started = next(data for name, data in events if name == "RunStarted")
        assert run_started["requestId"] == "req-agui-1"
        assert run_started["conversationId"] == "conv-agui-1"
        assert "runId" in run_started
        assert "timestamp" in run_started

        # Validate TextMessageContent and TextMessageEnd
        text_content = next(data for name, data in events if name == "TextMessageContent")
        assert text_content["delta"] == "Full AG-UI 1.0 turn reply"
        assert text_content["requestId"] == "req-agui-1"

        text_end = next(data for name, data in events if name == "TextMessageEnd")
        assert text_end["text"] == "Full AG-UI 1.0 turn reply"

        # Validate RunFinished
        run_finished = next(data for name, data in events if name == "RunFinished")
        assert run_finished["status"] == "completed"
        assert run_finished["requestId"] == "req-agui-1"
        assert run_finished["runId"] == run_started["runId"]


@pytest.mark.asyncio
async def test_web_ag_ui_1_0_mid_turn_incremental_streaming(memory_db: SqliteMemory) -> None:
    """Agent producing incremental token deltas emits multiple TextMessageContent events."""
    class IncrementalAgent:
        async def handle(
            self, session: str, request_id: str, text: str, approve: Any = None
        ) -> str:
            hook = get_turn_event_hook()
            if hook is not None:
                await hook("text_delta", {"delta": "Chunk 1: Hello "})
                await hook("text_delta", {"delta": "Chunk 2: world "})
                await hook("text_delta", {"delta": "Chunk 3: streaming!"})
            return "Chunk 1: Hello Chunk 2: world Chunk 3: streaming!"

    bus = MessageBus(IncrementalAgent(), web={"local"})
    app = create_app(bus=bus, memory=memory_db, token="fixture-token", user_id="local")

    headers = {
        "Authorization": "Bearer fixture-token",
        "Content-Type": "application/json",
    }
    payload = {
        "text": "stream test",
        "requestId": "req-stream-2",
        "conversationId": "conv-stream-2",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:3000") as client:
        res = await client.post("/api/chat/stream", json=payload, headers=headers)
        assert res.status_code == 200
        events = parse_sse_events(res.text)

        content_deltas = [
            data["delta"] for name, data in events if name == "TextMessageContent"
        ]
        assert content_deltas == [
            "Chunk 1: Hello ",
            "Chunk 2: world ",
            "Chunk 3: streaming!",
        ]

        text_end = next(data for name, data in events if name == "TextMessageEnd")
        assert text_end["text"] == "Chunk 1: Hello Chunk 2: world Chunk 3: streaming!"


@pytest.mark.asyncio
async def test_web_ag_ui_1_0_tool_calls_and_secret_masking(memory_db: SqliteMemory) -> None:
    """Tool-call events (ToolCallStart, ToolCallArgs, ToolCallEnd) never leak credentials."""
    class ToolUsingAgent:
        async def handle(
            self, session: str, request_id: str, text: str, approve: Any = None
        ) -> str:
            hook = get_turn_event_hook()
            if hook is not None:
                await hook(
                    "tool_call_start",
                    {
                        "id": "tc-123",
                        "name": "shell_exec",
                        "args": {
                            "command": "curl https://user:super_secret_pw@api.example.com",
                            "api_key": "sk-ant-api03-abcdef1234567890",
                            "auth_token": "ghp_githubsecrettoken1234567",
                            "aws_access": "AKIA1234567890ABCDEF",
                            "normal_param": "hello-world",
                        },
                    },
                )
                await hook(
                    "tool_call_end",
                    {
                        "id": "tc-123",
                        "name": "shell_exec",
                        "status": "success",
                        "result": "Logged in with Bearer my_jwt_token_secret_1234567890",
                    },
                )
            return "Command executed securely."

    bus = MessageBus(ToolUsingAgent(), web={"local"})
    app = create_app(bus=bus, memory=memory_db, token="fixture-token", user_id="local")

    headers = {
        "Authorization": "Bearer fixture-token",
        "Content-Type": "application/json",
    }
    payload = {
        "text": "run secret command",
        "requestId": "req-sec-3",
        "conversationId": "conv-sec-3",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:3000") as client:
        res = await client.post("/api/chat/stream", json=payload, headers=headers)
        assert res.status_code == 200
        raw_body = res.text
        events = parse_sse_events(raw_body)

        event_names = [e[0] for e in events]
        assert "ToolCallStart" in event_names
        assert "ToolCallArgs" in event_names
        assert "ToolCallEnd" in event_names

        # Verify args summary has secrets redacted
        args_data = next(data for name, data in events if name == "ToolCallArgs")
        assert args_data["toolName"] == "shell_exec"
        assert args_data["toolCallId"] == "tc-123"
        args = args_data["args"]
        assert args["api_key"] == "[REDACTED]"
        assert args["auth_token"] == "[REDACTED]"
        assert args["normal_param"] == "hello-world"
        assert "super_secret_pw" not in raw_body
        assert "sk-ant-api03" not in raw_body
        assert "ghp_githubsecrettoken" not in raw_body
        assert "AKIA1234567890ABCDEF" not in raw_body
        assert "my_jwt_token_secret" not in raw_body

        # Verify result summary has bearer token masked
        end_data = next(data for name, data in events if name == "ToolCallEnd")
        assert end_data["status"] == "success"
        assert "Bearer [REDACTED]" in end_data["result"]


@pytest.mark.asyncio
async def test_web_ag_ui_1_0_interactive_approval_lifecycle_and_yc05_checks(
    memory_db: SqliteMemory,
) -> None:
    """ActionCard events stream over SSE, and web approval endpoint enforces YC-05 protections."""
    approval_provider = InMemoryApprovalProvider()
    expected_command_args = {"query": "DROP TABLE users;"}

    class ApprovalDemandingAgent:
        async def handle(
            self, session: str, request_id: str, text: str, approve: Any = None
        ) -> str:
            card = ActionCard(
                approvalId="app-card-777",
                nonce="nonce-secret-777",
                toolId="db_execute",
                rendered=ActionCardRendered(
                    title="Database Drop Table",
                    exact="DROP TABLE users;",
                    effects=["write", "destroy"],
                    taint="clean",
                    reversible=False,
                ),
            )
            tc = ToolCall(id="call-db-777", name="db_execute", input=expected_command_args)
            assert approve is not None
            approved = await approve(tc, card)
            if approved:
                return "Table dropped successfully."
            return "Action was denied."

    bus = MessageBus(ApprovalDemandingAgent(), web={"local"})
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
    payload = {
        "text": "drop table",
        "requestId": "req-app-777",
        "conversationId": "conv-app-777",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:3000") as client:
        # Start SSE stream in background
        stream_task = asyncio.create_task(
            client.post("/api/chat/stream", json=payload, headers=headers)
        )

        # Allow SSE stream to start and register ActionCard
        await asyncio.sleep(0.05)

        # 1. Authorization requirement on approval endpoint -> 401
        res_no_auth = await client.post(
            "/api/chat/approval",
            json={"approvalId": "app-card-777", "nonce": "nonce-secret-777", "action": "approve"},
        )
        assert res_no_auth.status_code == 401

        # 2. Cross-origin denied -> 403
        res_origin = await client.post(
            "/api/chat/approval",
            json={"approvalId": "app-card-777", "nonce": "nonce-secret-777", "action": "approve"},
            headers={**headers, "Origin": "http://evil.example"},
        )
        assert res_origin.status_code == 403

        # 3. Content-Type application/json required -> 415
        res_ct = await client.post(
            "/api/chat/approval",
            content=b"not json",
            headers={"Authorization": "Bearer fixture-token", "Content-Type": "text/plain"},
        )
        assert res_ct.status_code == 415

        # 4. Invalid nonce rejected -> 400
        res_bad_nonce = await client.post(
            "/api/chat/approval",
            json={"approvalId": "app-card-777", "nonce": "wrong-nonce", "action": "approve"},
            headers=headers,
        )
        assert res_bad_nonce.status_code == 400
        assert "Invalid nonce" in res_bad_nonce.json()["error"]

        # 5. Argument tampering rejected -> 400
        res_tampered = await client.post(
            "/api/chat/approval",
            json={
                "approvalId": "app-card-777",
                "nonce": "nonce-secret-777",
                "action": "approve",
                "args": {"query": "DROP TABLE critical_data;"},
            },
            headers=headers,
        )
        assert res_tampered.status_code == 400
        assert "tampering" in res_tampered.json()["error"]

        # 6. Valid approval -> 200
        res_approve = await client.post(
            "/api/chat/approval",
            json={
                "approvalId": "app-card-777",
                "nonce": "nonce-secret-777",
                "action": "approve",
            },
            headers=headers,
        )
        assert res_approve.status_code == 200
        assert res_approve.json() == {"status": "approved", "approvalId": "app-card-777"}

        # Wait for SSE stream to complete
        stream_res = await stream_task
        assert stream_res.status_code == 200
        events = parse_sse_events(stream_res.text)

        event_names = [e[0] for e in events]
        assert "ActionCard" in event_names
        card_event = next(data for name, data in events if name == "ActionCard")
        assert card_event["approvalId"] == "app-card-777"
        assert card_event["rendered"]["title"] == "Database Drop Table"

        text_end = next(data for name, data in events if name == "TextMessageEnd")
        assert text_end["text"] == "Table dropped successfully."

        # 7. Replay attack rejection -> 400
        res_replay = await client.post(
            "/api/chat/approval",
            json={
                "approvalId": "app-card-777",
                "nonce": "nonce-secret-777",
                "action": "approve",
            },
            headers=headers,
        )
        assert res_replay.status_code == 400
        assert "Replay detected" in res_replay.json()["error"]


@pytest.mark.asyncio
async def test_web_ag_ui_1_0_denial_lifecycle(memory_db: SqliteMemory) -> None:
    """ActionCard denial resolves tool approval to False and rejects subsequent approve attempts."""
    approval_provider = InMemoryApprovalProvider()

    class DeniedAgent:
        async def handle(
            self, session: str, request_id: str, text: str, approve: Any = None
        ) -> str:
            card = ActionCard(
                approvalId="app-deny-999",
                nonce="nonce-deny-999",
                rendered=ActionCardRendered(
                    title="Dangerous Operation",
                    exact="rm -rf /",
                    effects=["destroy"],
                    taint="contaminated",
                    reversible=False,
                ),
            )
            tc = ToolCall(id="tc-deny-999", name="dangerous_tool", input={})
            assert approve is not None
            approved = await approve(tc, card)
            return "Operation approved." if approved else "Operation was denied by user."

    bus = MessageBus(DeniedAgent(), web={"local"})
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
    payload = {
        "text": "run dangerous op",
        "requestId": "req-deny-999",
        "conversationId": "conv-deny-999",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:3000") as client:
        stream_task = asyncio.create_task(
            client.post("/api/chat/stream", json=payload, headers=headers)
        )
        await asyncio.sleep(0.05)

        # Deny the card via POST /api/approval
        res_deny = await client.post(
            "/api/approval",
            json={"approvalId": "app-deny-999", "nonce": "nonce-deny-999", "action": "deny"},
            headers=headers,
        )
        assert res_deny.status_code == 200
        assert res_deny.json() == {"status": "denied", "approvalId": "app-deny-999"}

        stream_res = await stream_task
        assert stream_res.status_code == 200
        events = parse_sse_events(stream_res.text)
        text_end = next(data for name, data in events if name == "TextMessageEnd")
        assert text_end["text"] == "Operation was denied by user."

        # Subsequent approve attempt on denied card is rejected
        res_after = await client.post(
            "/api/approval",
            json={"approvalId": "app-deny-999", "nonce": "nonce-deny-999", "action": "approve"},
            headers=headers,
        )
        assert res_after.status_code == 400
        assert "denied" in res_after.json()["error"]


@pytest.mark.asyncio
async def test_web_ui_rendered_elements_and_approval_controls(
    memory_db: SqliteMemory,
) -> None:
    """Web HTML, CSS, and JS serve UI components for approvals, tools, and streaming."""
    agent = FakeAgent()
    bus = MessageBus(agent, web={"local"})
    app = create_app(bus=bus, memory=memory_db, token="fixture-token", user_id="local")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:3000") as client:
        # 1. HTML has approval and tool containers
        res_html = await client.get("/")
        assert res_html.status_code == 200
        assert '<div id="approvals"' in res_html.text
        assert '<div id="tools"' in res_html.text
        assert '<div id="replies"' in res_html.text
        assert "fixture-token" not in res_html.text

        # 2. CSS has card, tool, and button styles
        res_css = await client.get("/style.css")
        assert res_css.status_code == 200
        assert ".card" in res_css.text
        assert ".btn-approve" in res_css.text
        assert ".btn-deny" in res_css.text
        assert ".tool-call" in res_css.text

        # 3. JS has streaming reader, ActionCard renderer, and approval submission
        res_js = await client.get("/app.js")
        assert res_js.status_code == 200
        assert "renderCard" in res_js.text
        assert "appendTool" in res_js.text
        assert "/api/chat/stream" in res_js.text
        assert "/api/chat/approval" in res_js.text
        assert "textContent" in res_js.text
        assert "innerHTML" not in res_js.text
        assert "localStorage" not in res_js.text
        assert "sessionStorage" not in res_js.text


def test_sanitize_secret_patterns_and_args_summary() -> None:
    """Verifies that sensitive tokens, AWS keys, passwords, and authorization headers are masked."""
    # 1. Bearer tokens
    assert sanitize_secret_patterns("Bearer eyJhbGciOiJIUzI1NiJ9") == "Bearer [REDACTED]"
    # 2. AWS access keys
    assert sanitize_secret_patterns("Access key AKIAIOSFODNN7EXAMPLE") == "Access key [REDACTED_AWS_KEY]"
    # 3. Generic API keys
    assert sanitize_secret_patterns("GitHub token ghp_1234567890abcdef123456") == "GitHub token [REDACTED_TOKEN]"
    # 4. Embedded URL credentials
    assert sanitize_secret_patterns("http://admin:secret123@internal.net/api") == "http://***:***@internal.net/api"

    # 5. Nested dictionary and list args
    args = {
        "api_key": "secret-value",
        "nested": {"token": "top-secret", "safe": 123},
        "safe_list": ["clean", "Bearer 123456"],
        "normal_text": "hello",
    }
    sanitized = sanitize_args_summary(args)
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["nested"]["token"] == "[REDACTED]"
    assert sanitized["nested"]["safe"] == 123
    assert sanitized["safe_list"][1] == "Bearer [REDACTED]"
    assert sanitized["normal_text"] == "hello"


def test_approval_input_validation() -> None:
    """Verifies ApprovalInput Pydantic model parses aliases and rejects extra fields."""
    app_in1 = ApprovalInput.model_validate(
        {"approvalId": "app-1", "nonce": "nonce-1", "action": "approve"}
    )
    assert app_in1.approvalId == "app-1"
    assert app_in1.resolved_action == "approve"

    app_in2 = ApprovalInput.model_validate(
        {"approval_id": "app-2", "nonce": "nonce-2", "decision": "deny"}
    )
    assert app_in2.approval_id == "app-2"
    assert app_in2.resolved_action == "deny"

    with pytest.raises(ValidationError):
        ApprovalInput.model_validate(
            {"approvalId": "app-3", "nonce": "nonce-3", "extra_bad_field": "disallowed"}
        )


