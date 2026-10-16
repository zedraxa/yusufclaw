"""Tests for FastAPI web gateway conforming to TypeScript spec and AG-UI SSE streaming."""

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from yusufclaw.gateway.bus import MessageBus
from yusufclaw.gateway.web import (
    RoutineTarget,
    WebAdapter,
    create_app,
)
from yusufclaw.memory.sqlite import SqliteMemory


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
