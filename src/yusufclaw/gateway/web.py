"""FastAPI web gateway for YusufClaw with AG-UI SSE streaming and minimal web UI.

Provides:
- GET /: Minimal chat UI HTML page
- GET /app.js: Client application JavaScript
- GET /style.css: Minimal stylesheets
- GET /health: System health and model provider status probe
- GET /api/routines: Authenticated web inbox for scheduled replies
- POST /api/chat: Authenticated conversational endpoint (JSON or SSE)
- POST /api/chat/stream: Dedicated AG-UI event stream endpoint (SSE)
- GET /api/events: SSE real-time gateway event stream
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
import re
import secrets
import socket
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from yusufclaw import __version__
from yusufclaw.agent import reset_turn_event_hook, set_turn_event_hook
from yusufclaw.config import Settings, get_settings
from yusufclaw.gateway.approval import (
    ActionCard,
    ActionCardRendered,
    ApprovalProvider,
    InMemoryApprovalProvider,
)
from yusufclaw.gateway.bus import IncomingMessage, MessageBus
from yusufclaw.gateway.web_page import CHAT_PAGE, CHAT_SCRIPT, CHAT_STYLE
from yusufclaw.telemetry import setup_telemetry

SENSITIVE_KEY_SUBSTRINGS = {
    "password",
    "secret",
    "token",
    "key",
    "auth",
    "credential",
    "credentials",
    "api_key",
    "apikey",
    "access_token",
    "private_key",
    "webhook",
    "bearer",
}
RE_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._~+/-]+", re.IGNORECASE)
RE_AWS_KEY = re.compile(r"AKIA[0-9A-Z]{16}")
RE_GENERIC_KEY = re.compile(r"(?:(?:sk|ghp|gho|glpat|github_pat)[-_]|sk-proj-)[A-Za-z0-9_-]{16,}")
RE_URL_CRED = re.compile(r"(https?://)([^:]+):([^@]+)@")


def sanitize_secret_patterns(text: str) -> str:
    """Mask credentials and secrets within freeform strings."""
    t = RE_BEARER.sub("Bearer [REDACTED]", text)
    t = RE_AWS_KEY.sub("[REDACTED_AWS_KEY]", t)
    t = RE_GENERIC_KEY.sub("[REDACTED_TOKEN]", t)
    t = RE_URL_CRED.sub(r"\1***:***@", t)
    return t


def sanitize_args_summary(args: Any) -> Any:
    """Sanitize and truncate tool arguments to prevent credential or secret leaks."""
    if isinstance(args, dict):
        sanitized: dict[str, Any] = {}
        for k, v in args.items():
            k_str = str(k).lower()
            if any(sub in k_str for sub in SENSITIVE_KEY_SUBSTRINGS):
                sanitized[k] = "[REDACTED]"
            elif isinstance(v, (dict, list)):
                sanitized[k] = sanitize_args_summary(v)
            elif isinstance(v, str):
                s = sanitize_secret_patterns(v)
                if len(s) > 300:
                    s = s[:300] + "... (truncated)"
                sanitized[k] = s
            else:
                sanitized[k] = v
        return sanitized
    if isinstance(args, list):
        return [sanitize_args_summary(item) for item in args]
    if isinstance(args, str):
        s = sanitize_secret_patterns(args)
        if len(s) > 300:
            s = s[:300] + "... (truncated)"
        return s
    return args


def sanitize_result_summary(result: Any) -> str:
    """Sanitize and truncate tool execution result text."""
    text = str(result)
    cleaned = sanitize_secret_patterns(text)
    if len(cleaned) > 500:
        cleaned = cleaned[:500] + "... (truncated)"
    return cleaned


class HealthResponse(BaseModel):
    """Health check response schema."""

    status: str
    version: str
    service: str
    model_provider: str


class ApprovalInput(BaseModel):
    """Strict schema for incoming action card approval or denial decisions."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    approval_id: str = Field(min_length=1, max_length=128, alias="approvalId")
    nonce: str = Field(min_length=1, max_length=128)
    decision: Literal["approve", "deny"] = Field(default="approve")
    action: Literal["approve", "deny"] | None = None
    args: dict[str, Any] | None = None

    @property
    def approvalId(self) -> str:
        return self.approval_id

    @property
    def resolved_action(self) -> Literal["approve", "deny"]:
        return self.action or self.decision


class ChatMessageInput(BaseModel):
    """Strict schema for incoming chat requests."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    text: str = Field(min_length=1, max_length=16000)
    conversation_id: str = Field(min_length=1, max_length=128, alias="conversationId")
    request_id: str = Field(min_length=1, max_length=128, alias="requestId")

    @property
    def conversationId(self) -> str:
        return self.conversation_id

    @property
    def requestId(self) -> str:
        return self.request_id


class RoutineTarget(BaseModel):
    """Delivery target for scheduled routine messages."""

    model_config = ConfigDict(populate_by_name=True)

    channel: Literal["web", "telegram", "cli"]
    user_id: str = Field(alias="userId")
    conversation_id: str = Field(alias="conversationId")

    @property
    def userId(self) -> str:
        return self.user_id

    @property
    def conversationId(self) -> str:
        return self.conversation_id


class InboxItem(BaseModel):
    """Inbox reply payload."""

    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field(alias="requestId")
    conversation_id: str = Field(alias="conversationId")
    reply: str

    @property
    def requestId(self) -> str:
        return self.request_id

    @property
    def conversationId(self) -> str:
        return self.conversation_id


class WebAdapterOptions(BaseModel):
    """Configuration options for WebAdapter."""

    model_config = ConfigDict(populate_by_name=True)

    token: str
    user_id: str = Field(default="local", alias="userId")
    host: str = "127.0.0.1"
    port: int = 3000

    @property
    def userId(self) -> str:
        return self.user_id


def format_sse_event(event: str, data: dict[str, Any] | str) -> str:
    """Format an AG-UI Server-Sent Event."""
    payload = json.dumps(data) if isinstance(data, dict) else str(data)
    return f"event: {event}\ndata: {payload}\n\n"


def create_app(
    bus: MessageBus | Settings | None = None,
    memory: Any | None = None,
    token: str | None = None,
    user_id: str = "local",
    settings: Settings | None = None,
    approval_provider: ApprovalProvider | None = None,
) -> FastAPI:
    """Create and configure FastAPI web gateway application."""
    actual_bus: MessageBus | None
    if isinstance(bus, Settings):
        cfg = bus
        actual_bus = None
    else:
        cfg = settings or get_settings()
        actual_bus = bus
    auth_token = token if token is not None else cfg.web_auth_token
    token_hash = (
        hashlib.sha256(auth_token.encode("utf-8")).digest()
        if auth_token
        else None
    )

    active_approval_provider: ApprovalProvider
    bus_agent = getattr(actual_bus, "agent", None) if actual_bus is not None else None
    agent_store = getattr(bus_agent, "card_store", None)
    if approval_provider is not None:
        active_approval_provider = approval_provider
    elif agent_store is not None and isinstance(agent_store, ApprovalProvider):
        active_approval_provider = agent_store
    else:
        active_approval_provider = InMemoryApprovalProvider()

    pending_approvals: dict[str, dict[str, Any]] = {}

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

    # Security Headers Middleware
    @app.middleware("http")
    async def add_security_headers_and_check_origin(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        origin = request.headers.get("origin")
        host = request.headers.get("host")
        if origin:
            allowed_origins = [f"http://{host}", f"https://{host}"]
            if origin not in allowed_origins:
                resp = JSONResponse(
                    status_code=403,
                    content={"error": "Origin denied"},
                )
                _apply_security_headers(resp)
                return resp

        response = await call_next(request)
        _apply_security_headers(response)
        return response

    def _apply_security_headers(response: Response) -> None:
        response.headers["cache-control"] = "no-store"
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["referrer-policy"] = "no-referrer"
        response.headers["content-security-policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'"
        )

    def _verify_auth(request: Request) -> None:
        if token_hash is None:
            raise HTTPException(status_code=401, detail={"error": "Unauthorized"})

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail={"error": "Unauthorized"})

        supplied = auth_header[7:].strip()
        supplied_hash = hashlib.sha256(supplied.encode("utf-8")).digest()
        if not hmac.compare_digest(token_hash, supplied_hash):
            raise HTTPException(status_code=401, detail={"error": "Unauthorized"})

    @app.get("/", response_class=HTMLResponse)
    async def get_index() -> HTMLResponse:
        return HTMLResponse(content=CHAT_PAGE, media_type="text/html; charset=utf-8")

    @app.get("/app.js", response_class=PlainTextResponse)
    async def get_script() -> PlainTextResponse:
        return PlainTextResponse(
            content=CHAT_SCRIPT, media_type="text/javascript; charset=utf-8"
        )

    @app.get("/style.css", response_class=PlainTextResponse)
    async def get_style() -> PlainTextResponse:
        return PlainTextResponse(
            content=CHAT_STYLE, media_type="text/css; charset=utf-8"
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

    @app.get("/api/routines")
    async def get_routines(request: Request) -> JSONResponse:
        """Fetch scheduled replies delivered to web inbox."""
        _verify_auth(request)
        inbox_key = f"web-inbox:{user_id}"
        items: list[dict[str, Any]] = []
        if memory is not None:
            if hasattr(memory, "get_state"):
                raw = memory.get_state(inbox_key)
            elif hasattr(memory, "getState"):
                raw = memory.getState(inbox_key)
            else:
                raw = None
            if inspect.isawaitable(raw):
                raw = await raw
            if raw:
                try:
                    items = json.loads(raw)
                except Exception:
                    items = []
        return JSONResponse(status_code=200, content=items)

    @app.get("/api/roles")
    async def get_roles(request: Request) -> JSONResponse:
        """List specialist agent roles with capability matrices and A2A cards."""
        _verify_auth(request)
        from yusufclaw.roles import get_role_catalog

        catalog = get_role_catalog()
        roles_data = [
            {
                "roleId": r.role_id,
                "name": r.name,
                "description": r.description,
                "aliases": r.aliases,
                "modelTier": r.model_tier,
                "riskLevel": r.risk_level,
                "allowedTools": r.allowed_tools,
                "allowedMcpServers": r.allowed_mcp_servers,
                "skills": r.skills,
                "memoryScopes": r.memory_scopes.model_dump(),
                "approvalActions": r.approval_actions,
                "budgetLimits": r.budget_limits.model_dump(),
                "sandboxRequired": r.sandbox_required,
                "sandboxMode": r.sandbox_mode,
                "sendChannels": r.send_channels,
                "a2aCard": r.to_a2a_card().model_dump(),
            }
            for r in catalog.list_roles()
        ]
        return JSONResponse(status_code=200, content=roles_data)

    async def _parse_chat_body(request: Request) -> ChatMessageInput:
        content_type = request.headers.get("content-type", "").split(";")[0].strip()
        if content_type != "application/json":
            raise HTTPException(status_code=415, detail={"error": "JSON required"})

        body = await request.body()
        if len(body) > 100_000:
            raise HTTPException(status_code=413, detail={"error": "Request too large"})

        try:
            raw_data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail={"error": "Invalid message"}) from exc

        try:
            return ChatMessageInput.model_validate(raw_data)
        except Exception as exc:
            raise HTTPException(status_code=400, detail={"error": "Invalid message"}) from exc

    async def _handle_approval_decision(request: Request) -> JSONResponse:
        _verify_auth(request)

        content_type = request.headers.get("content-type", "").split(";")[0].strip()
        if content_type != "application/json":
            raise HTTPException(status_code=415, detail={"error": "JSON required"})

        body = await request.body()
        if len(body) > 100_000:
            raise HTTPException(status_code=413, detail={"error": "Request too large"})

        try:
            raw_data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail={"error": "Invalid approval payload"}
            ) from exc

        try:
            app_in = ApprovalInput.model_validate(raw_data)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail={"error": "Invalid approval payload"}
            ) from exc

        app_id = app_in.approval_id
        pending = pending_approvals.get(app_id)
        record = active_approval_provider.get(app_id)

        if pending is None:
            if record and record.status in ("approved", "consumed"):
                return JSONResponse(
                    status_code=400,
                    content={"error": "Replay detected: action card already consumed"},
                )
            if record and record.status == "denied":
                return JSONResponse(
                    status_code=400,
                    content={"error": "Action card was denied"},
                )
            return JSONResponse(
                status_code=404,
                content={"error": "Approval request not found or expired"},
            )

        if app_in.resolved_action == "approve":
            check_args = (
                app_in.args if app_in.args is not None else pending.get("expected_args")
            )
            res = active_approval_provider.consume(
                approval_id=app_id,
                nonce=app_in.nonce,
                actual_args=check_args,
            )
            if not res.success:
                return JSONResponse(
                    status_code=400,
                    content={"error": res.reason or "Approval failed"},
                )

            fut = pending.get("future")
            if fut and not fut.done():
                fut.set_result(True)

            return JSONResponse(
                status_code=200,
                content={"status": "approved", "approvalId": app_id},
            )
        else:
            active_approval_provider.deny(app_id, app_in.nonce)
            fut = pending.get("future")
            if fut and not fut.done():
                fut.set_result(False)

            return JSONResponse(
                status_code=200,
                content={"status": "denied", "approvalId": app_id},
            )

    @app.post("/api/chat/approval")
    async def chat_approval(request: Request) -> JSONResponse:
        """Handle human action approval/denial for chat turns."""
        return await _handle_approval_decision(request)

    @app.post("/api/approval")
    async def approval(request: Request) -> JSONResponse:
        """Alias endpoint for human action approval/denial."""
        return await _handle_approval_decision(request)

    @app.post("/api/chat")
    async def chat(
        request: Request, accept: str | None = Header(default=None)
    ) -> Response:
        """Send chat message to agent via MessageBus, supporting JSON and SSE streaming."""
        _verify_auth(request)
        msg_in = await _parse_chat_body(request)

        if actual_bus is None:
            return JSONResponse(
                status_code=500, content={"error": "Message bus is not configured"}
            )

        # Stream SSE if client requests text/event-stream
        if accept and "text/event-stream" in accept:
            return StreamingResponse(
                _stream_chat_events(
                    bus=actual_bus,
                    user_id=user_id,
                    msg_in=msg_in,
                    approval_provider=active_approval_provider,
                    pending_approvals=pending_approvals,
                ),
                media_type="text/event-stream",
            )

        # Standard synchronous turn
        try:
            reply = await actual_bus.dispatch(
                IncomingMessage(
                    channel="web",
                    userId=user_id,
                    conversationId=msg_in.conversation_id,
                    requestId=msg_in.request_id,
                    text=msg_in.text,
                )
            )
            return JSONResponse(status_code=200, content={"reply": reply})
        except Exception:
            # Mask internal exceptions and URLs
            return JSONResponse(
                status_code=502,
                content={
                    "error": (
                        "Request failed. Check provider configuration and connectivity."
                    )
                },
            )

    @app.post("/api/chat/stream")
    async def chat_stream(request: Request) -> StreamingResponse:
        """Dedicated AG-UI Server-Sent Events stream endpoint."""
        _verify_auth(request)
        msg_in = await _parse_chat_body(request)

        if actual_bus is None:
            raise HTTPException(
                status_code=500, detail={"error": "Message bus is not configured"}
            )

        return StreamingResponse(
            _stream_chat_events(
                bus=actual_bus,
                user_id=user_id,
                msg_in=msg_in,
                approval_provider=active_approval_provider,
                pending_approvals=pending_approvals,
            ),
            media_type="text/event-stream",
        )

    @app.get("/api/events")
    async def events_stream(request: Request) -> StreamingResponse:
        """SSE endpoint streaming live system events."""
        _verify_auth(request)

        async def event_generator() -> AsyncIterator[str]:
            yield format_sse_event(
                "connected",
                {"status": "connected", "service": "yusufclaw", "user_id": user_id},
            )
            try:
                from yusufclaw.roles import get_role_catalog

                catalog = get_role_catalog()
                roles_summary = [
                    {
                        "roleId": r.role_id,
                        "name": r.name,
                        "modelTier": r.model_tier,
                        "riskLevel": r.risk_level,
                    }
                    for r in catalog.list_roles()
                ]
                yield format_sse_event("roles", {"roles": roles_summary})
            except Exception:
                pass

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    return app


async def _stream_chat_events(
    bus: MessageBus,
    user_id: str,
    msg_in: ChatMessageInput,
    approval_provider: ApprovalProvider | None = None,
    pending_approvals: dict[str, dict[str, Any]] | None = None,
) -> AsyncIterator[str]:
    """Emit AG-UI 1.0 style Server-Sent Events (run lifecycle, progress, tools, approvals, text deltas, done)."""
    run_id = f"run-{uuid.uuid4()}"
    msg_id = f"msg-{uuid.uuid4()}"
    event_queue: asyncio.Queue[str | None] = asyncio.Queue()
    emitted_text_start = False
    emitted_any_delta = False

    # Emit RunStarted and progress immediately
    yield format_sse_event(
        "RunStarted",
        {
            "runId": run_id,
            "requestId": msg_in.request_id,
            "conversationId": msg_in.conversation_id,
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )
    yield format_sse_event(
        "progress",
        {
            "status": "thinking",
            "step": 1,
            "requestId": msg_in.request_id,
            "conversationId": msg_in.conversation_id,
        },
    )

    async def turn_hook(event_name: str, payload: dict[str, Any]) -> None:
        nonlocal emitted_text_start, emitted_any_delta
        if event_name == "tool_call_start":
            call_id = payload.get("id") or str(uuid.uuid4())
            tool_name = payload.get("name", "")
            clean_args = sanitize_args_summary(payload.get("args", {}))
            await event_queue.put(
                format_sse_event(
                    "ToolCallStart",
                    {
                        "toolCallId": call_id,
                        "toolName": tool_name,
                        "requestId": msg_in.request_id,
                        "conversationId": msg_in.conversation_id,
                    },
                )
            )
            await event_queue.put(
                format_sse_event(
                    "ToolCallArgs",
                    {
                        "toolCallId": call_id,
                        "toolName": tool_name,
                        "args": clean_args,
                        "requestId": msg_in.request_id,
                        "conversationId": msg_in.conversation_id,
                    },
                )
            )
        elif event_name == "tool_call_end":
            call_id = payload.get("id") or ""
            tool_name = payload.get("name", "")
            status = payload.get("status", "success")
            clean_res = sanitize_result_summary(payload.get("result", ""))
            await event_queue.put(
                format_sse_event(
                    "ToolCallEnd",
                    {
                        "toolCallId": call_id,
                        "toolName": tool_name,
                        "status": status,
                        "result": clean_res,
                        "requestId": msg_in.request_id,
                        "conversationId": msg_in.conversation_id,
                    },
                )
            )
        elif event_name == "text_delta":
            delta = payload.get("delta") or payload.get("text") or ""
            if delta:
                if not emitted_text_start:
                    emitted_text_start = True
                    await event_queue.put(
                        format_sse_event(
                            "TextMessageStart",
                            {
                                "messageId": msg_id,
                                "role": "assistant",
                                "requestId": msg_in.request_id,
                                "conversationId": msg_in.conversation_id,
                            },
                        )
                    )
                emitted_any_delta = True
                await event_queue.put(
                    format_sse_event(
                        "TextMessageContent",
                        {
                            "messageId": msg_id,
                            "delta": delta,
                            "requestId": msg_in.request_id,
                            "conversationId": msg_in.conversation_id,
                        },
                    )
                )

    async def web_approve(call: Any, card: Any = None) -> bool:
        action_card: ActionCard | None = None
        if isinstance(card, ActionCard):
            action_card = card
        elif isinstance(card, dict):
            action_card = ActionCard.model_validate(card)

        if action_card is None:
            call_name = getattr(call, "name", "tool")
            call_args = getattr(call, "input", {})
            action_card = ActionCard(
                approvalId=str(uuid.uuid4()),
                nonce=secrets.token_hex(16),
                toolId=call_name,
                rendered=ActionCardRendered(
                    title=f"Execute {call_name}",
                    exact=json.dumps(call_args),
                    effects=["exec"],
                    taint="clean",
                    reversible=False,
                ),
            )

        call_input = getattr(call, "input", {})
        if approval_provider is not None:
            approval_provider.register(action_card, input_args=call_input)

        card_dict = action_card.model_dump(by_alias=True)
        card_dict["requestId"] = msg_in.request_id
        card_dict["conversationId"] = msg_in.conversation_id

        await event_queue.put(format_sse_event("ActionCard", card_dict))
        await event_queue.put(format_sse_event("approval", card_dict))

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[bool] = loop.create_future()
        app_id = action_card.approval_id

        if pending_approvals is not None:
            pending_approvals[app_id] = {
                "future": fut,
                "card": action_card,
                "expected_args": call_input,
            }

        try:
            return await asyncio.wait_for(fut, timeout=300.0)
        except (TimeoutError, asyncio.CancelledError):
            return False
        finally:
            if pending_approvals is not None:
                pending_approvals.pop(app_id, None)

    async def dispatch_worker() -> None:
        try:
            token = set_turn_event_hook(turn_hook)
            try:
                reply = await bus.dispatch(
                    IncomingMessage(
                        channel="web",
                        userId=user_id,
                        conversationId=msg_in.conversation_id,
                        requestId=msg_in.request_id,
                        text=msg_in.text,
                    ),
                    approve=web_approve,
                )
            finally:
                reset_turn_event_hook(token)

            # Fallback if agent did not emit incremental deltas
            if not emitted_text_start:
                await event_queue.put(
                    format_sse_event(
                        "TextMessageStart",
                        {
                            "messageId": msg_id,
                            "role": "assistant",
                            "requestId": msg_in.request_id,
                            "conversationId": msg_in.conversation_id,
                        },
                    )
                )
            if not emitted_any_delta:
                await event_queue.put(
                    format_sse_event(
                        "TextMessageContent",
                        {
                            "messageId": msg_id,
                            "delta": reply,
                            "requestId": msg_in.request_id,
                            "conversationId": msg_in.conversation_id,
                        },
                    )
                )

            await event_queue.put(
                format_sse_event(
                    "TextMessageEnd",
                    {
                        "messageId": msg_id,
                        "text": reply,
                        "requestId": msg_in.request_id,
                        "conversationId": msg_in.conversation_id,
                    },
                )
            )
            # Legacy message event for backward compatibility
            await event_queue.put(
                format_sse_event(
                    "message",
                    {
                        "role": "assistant",
                        "reply": reply,
                        "requestId": msg_in.request_id,
                        "conversationId": msg_in.conversation_id,
                    },
                )
            )
            # Legacy done event for backward compatibility
            await event_queue.put(
                format_sse_event(
                    "done",
                    {
                        "status": "completed",
                        "requestId": msg_in.request_id,
                        "conversationId": msg_in.conversation_id,
                    },
                )
            )
            # AG-UI RunFinished event
            await event_queue.put(
                format_sse_event(
                    "RunFinished",
                    {
                        "runId": run_id,
                        "requestId": msg_in.request_id,
                        "conversationId": msg_in.conversation_id,
                        "status": "completed",
                    },
                )
            )
        except Exception as exc:
            err_msg = "Request failed. Check provider configuration and connectivity."
            await event_queue.put(
                format_sse_event(
                    "error",
                    {
                        "error": err_msg,
                        "detail": str(exc),
                        "requestId": msg_in.request_id,
                        "conversationId": msg_in.conversation_id,
                    },
                )
            )
            await event_queue.put(
                format_sse_event(
                    "RunFinished",
                    {
                        "runId": run_id,
                        "requestId": msg_in.request_id,
                        "conversationId": msg_in.conversation_id,
                        "status": "error",
                        "error": err_msg,
                    },
                )
            )
        finally:
            await event_queue.put(None)

    task = asyncio.create_task(dispatch_worker())
    try:
        while True:
            item = await event_queue.get()
            if item is None:
                break
            yield item
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class ServerAddress:
    """Represents a bound network address with address string and port number."""

    def __init__(self, address: str, port: int) -> None:
        self.address = address
        self.port = port


class ServerWrapper:
    """Wrapper exposing server address, listening state, and connection handling."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self.listening = False

    def address(self) -> ServerAddress:
        return ServerAddress(self._host, self._port)


class WebAdapter:
    """Adapter managing web server lifecycle and scheduled delivery inbox."""

    def __init__(
        self,
        bus: MessageBus,
        memory: Any,
        options: dict[str, Any] | WebAdapterOptions,
        approval_provider: ApprovalProvider | None = None,
    ) -> None:
        if isinstance(options, dict):
            opts = WebAdapterOptions.model_validate(options)
        else:
            opts = options

        if not opts.token or not opts.token.strip():
            raise ValueError("WEB_AUTH_TOKEN is required")

        self.bus = bus
        self.memory = memory
        self.options = opts
        self.approval_provider = approval_provider
        self.app = create_app(
            bus=bus,
            memory=memory,
            token=opts.token,
            user_id=opts.user_id,
            approval_provider=approval_provider,
        )

        self._host = opts.host
        self._port = opts.port
        self._server_task: asyncio.Task[None] | None = None
        self._uvicorn_server: uvicorn.Server | None = None
        self._bound_socket: socket.socket | None = None
        self.server = ServerWrapper(self._host, self._port)

    def inbox_key(self) -> str:
        return f"web-inbox:{self.options.user_id}"

    def inbox(self) -> list[dict[str, Any]]:
        raw = None
        if hasattr(self.memory, "get_state"):
            raw = self.memory.get_state(self.inbox_key())
        elif hasattr(self.memory, "getState"):
            raw = self.memory.getState(self.inbox_key())

        if raw is None:
            return []
        try:
            return json.loads(raw)  # type: ignore[no-any-return]
        except Exception:
            return []

    async def deliver(
        self,
        target: RoutineTarget | dict[str, Any],
        reply: str,
        request_id: str,
    ) -> None:
        """Deliver a scheduled routine response to the web user's inbox."""
        tgt = (
            RoutineTarget.model_validate(target)
            if isinstance(target, dict)
            else target
        )
        if tgt.channel != "web" or tgt.user_id != self.options.user_id:
            raise ValueError("Invalid web delivery target")

        items = self.inbox()
        exists = any(
            item.get("requestId") == request_id
            and item.get("conversationId") == tgt.conversation_id
            for item in items
        )
        if not exists:
            items.append(
                {
                    "requestId": request_id,
                    "conversationId": tgt.conversation_id,
                    "reply": reply,
                }
            )
            saved = json.dumps(items[-100:])
            if hasattr(self.memory, "set_state"):
                res = self.memory.set_state(self.inbox_key(), saved)
                if inspect.isawaitable(res):
                    await res
            elif hasattr(self.memory, "setState"):
                res = self.memory.setState(self.inbox_key(), saved)
                if inspect.isawaitable(res):
                    await res

    async def listen(self) -> None:
        """Bind socket and start Uvicorn server in background asyncio task."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self._host, self._port))
        sock.listen(128)
        sock.setblocking(False)

        bound_port = sock.getsockname()[1]
        self._bound_socket = sock
        self._port = bound_port
        self.server._port = bound_port
        self.server.listening = True

        config = uvicorn.Config(
            app=self.app,
            log_level="warning",
            lifespan="on",
        )
        self._uvicorn_server = uvicorn.Server(config)

        started = asyncio.Event()

        async def run_server() -> None:
            started.set()
            assert self._uvicorn_server is not None
            await self._uvicorn_server.serve(sockets=[sock])

        self._server_task = asyncio.create_task(run_server())
        await started.wait()
        # Brief yield to allow uvicorn's event loop startup
        await asyncio.sleep(0.05)

    async def close(self) -> None:
        """Gracefully stop server and abort in-flight connections."""
        self.server.listening = False
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
        if self._server_task is not None:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
        if self._bound_socket is not None:
            try:
                self._bound_socket.close()
            except Exception:
                pass


app = create_app()

__all__ = [
    "ApprovalInput",
    "ChatMessageInput",
    "HealthResponse",
    "InboxItem",
    "RoutineTarget",
    "ServerAddress",
    "ServerWrapper",
    "WebAdapter",
    "WebAdapterOptions",
    "app",
    "create_app",
    "format_sse_event",
    "sanitize_args_summary",
    "sanitize_result_summary",
    "sanitize_secret_patterns",
]
