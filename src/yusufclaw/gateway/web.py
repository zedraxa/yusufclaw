"""FastAPI web gateway for YusufClaw with AG-UI SSE streaming and minimal web UI.

Provides:
- GET /: Minimal chat UI HTML page
- GET /app.js: Client application JavaScript
- GET /style.css: Minimal stylesheets
- GET /health: System health and model provider status probe
- GET /api/routines: Authenticated web inbox for scheduled replies
- GET /api/memory: Memory inspection canvas (profile, facts, journal) (UX-07)
- POST /api/memory/forget: Deterministic unlearning with tombstone (UX-07)
- PATCH /api/memory/{record_id}: In-place claim edit with CAS (UX-07)
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
from yusufclaw.a2a.hook import TeammateStatusHook
from yusufclaw.a2a.registry import TeammateRegistry
from yusufclaw.agent import reset_turn_event_hook, set_turn_event_hook
from yusufclaw.config import Settings, get_settings
from yusufclaw.gateway.approval import (
    ActionCard,
    ActionCardRendered,
    ApprovalProvider,
    InMemoryApprovalProvider,
)
from yusufclaw.gateway.bus import IncomingMessage, MessageBus
from yusufclaw.gateway.credentials import (
    CredentialBroker,
    get_credential_broker,
)
from yusufclaw.gateway.models import (
    AG_UI_PROTOCOL_VERSION,
    AgentPersona,
    AgUiEnvelope,
    MemoryForgetInput,
    MemoryReviseApiInput,
    MilestoneStep,
    QuestionOption,
    QuestionWidget,
    SecretRequestWidget,
    SecretSubmitInput,
    WidgetResolveInput,
)
from yusufclaw.gateway.web_page import CHAT_PAGE, CHAT_SCRIPT, CHAT_STYLE
from yusufclaw.gateway.workspace import (
    AutomationStore,
    UsageMeter,
    serialize_connectors,
    serialize_teammates,
    validate_routine_payload,
)
from yusufclaw.mcp.models import MCPConfig
from yusufclaw.mcp.pinning import PinningStore
from yusufclaw.memory.models import ListOptions, MemoryKind, ReviseInput
from yusufclaw.memory.store import resolve as resolve_memory
from yusufclaw.routines.models import Routine
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
    edited_args: dict[str, Any] | None = Field(default=None, alias="editedArgs")

    @property
    def approvalId(self) -> str:
        return self.approval_id

    @property
    def resolved_action(self) -> Literal["approve", "deny"]:
        return self.action or self.decision

    @property
    def resolved_args(self) -> dict[str, Any] | None:
        return self.edited_args if self.edited_args is not None else self.args


class ChatMessageInput(BaseModel):
    """Strict schema for incoming chat requests."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    text: str = Field(min_length=1, max_length=16000)
    conversation_id: str = Field(min_length=1, max_length=128, alias="conversationId")
    request_id: str = Field(min_length=1, max_length=128, alias="requestId")
    agent_id: str | None = Field(default=None, max_length=64, alias="agentId")

    @property
    def conversationId(self) -> str:
        return self.conversation_id

    @property
    def requestId(self) -> str:
        return self.request_id

    @property
    def agentId(self) -> str:
        return self.agent_id or "orchestrator"


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


def format_agui_event(
    event_type: str,
    payload: dict[str, Any],
    run_id: str,
    session_id: str,
    agent_id: str = "orchestrator",
    sequence: int = 1,
    event_id: str | None = None,
) -> str:
    """Format an AG-UI 1.0 Server-Sent Event frame with standard envelope."""
    evt_id = event_id or f"evt_{uuid.uuid4().hex[:18]}"
    ts = datetime.now(UTC).isoformat()
    merged = dict(payload)
    merged.update(
        {
            "protocolVersion": AG_UI_PROTOCOL_VERSION,
            "eventId": evt_id,
            "runId": run_id,
            "sessionId": session_id,
            "agentId": agent_id,
            "timestamp": ts,
            "sequence": sequence,
            "eventType": event_type,
            "payload": payload,
        }
    )
    return f"event: {event_type}\ndata: {json.dumps(merged)}\n\n"


def create_app(
    bus: MessageBus | Settings | None = None,
    memory: Any | None = None,
    token: str | None = None,
    user_id: str = "local",
    settings: Settings | None = None,
    approval_provider: ApprovalProvider | None = None,
    credential_broker: CredentialBroker | None = None,
    mcp_config: MCPConfig | None = None,
    pinning_store: PinningStore | None = None,
    teammate_registry: TeammateRegistry | None = None,
    teammate_hook: TeammateStatusHook | None = None,
    routines: list[Routine] | None = None,
    usage_meter: UsageMeter | None = None,
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

    active_credential_broker: CredentialBroker = (
        credential_broker if credential_broker is not None else get_credential_broker()
    )
    active_mcp_config = mcp_config or MCPConfig()
    active_pinning = pinning_store or PinningStore()
    active_teammate_registry = teammate_registry
    active_teammate_hook = teammate_hook or TeammateStatusHook()
    active_usage = usage_meter or UsageMeter(memory=memory)
    automation_store = AutomationStore(memory=memory, seed=list(routines or []))
    pending_approvals: dict[str, dict[str, Any]] = {}
    pending_questions: dict[str, dict[str, Any]] = {}
    resolved_questions: dict[str, dict[str, Any]] = {}
    pending_secrets: dict[str, asyncio.Future[dict[str, Any]]] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        setup_telemetry(
            service_name=cfg.otel_service_name,
            otlp_endpoint=cfg.otel_exporter_otlp_endpoint,
        )
        await active_usage.load_monthly()
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
                app_in.resolved_args
                if app_in.resolved_args is not None
                else pending.get("expected_args")
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

    @app.post("/api/actions/resolve-approval")
    async def resolve_approval(request: Request) -> JSONResponse:
        """AG-UI action endpoint for human action approval/denial (UX-01, UX-05)."""
        return await _handle_approval_decision(request)

    @app.post("/api/chat/approval")
    async def chat_approval(request: Request) -> JSONResponse:
        """Handle human action approval/denial for chat turns."""
        return await _handle_approval_decision(request)

    @app.post("/api/approval")
    async def approval(request: Request) -> JSONResponse:
        """Alias endpoint for human action approval/denial."""
        return await _handle_approval_decision(request)

    @app.post("/api/actions/resolve-widget")
    async def resolve_widget(request: Request) -> JSONResponse:
        """AG-UI action endpoint for resolving inline question widgets (UX-04)."""
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
                status_code=400, detail={"error": "Invalid widget payload"}
            ) from exc

        try:
            widget_in = WidgetResolveInput.model_validate(raw_data)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail={"error": "Invalid widget payload"}
            ) from exc

        q_id = widget_in.question_id
        if q_id in resolved_questions:
            return JSONResponse(
                status_code=400,
                content={"error": "Replay detected: question already resolved"},
            )

        pending = pending_questions.get(q_id)
        if pending is None:
            return JSONResponse(
                status_code=404,
                content={"error": "Question not found or expired"},
            )

        fut = pending.get("future")
        res_payload = {
            "questionId": q_id,
            "selectedOptionIds": widget_in.selected_option_ids,
            "customText": widget_in.custom_text,
        }
        resolved_questions[q_id] = res_payload
        if fut and not fut.done():
            fut.set_result(res_payload)

        return JSONResponse(
            status_code=200,
            content={"status": "resolved", **res_payload},
        )

    @app.post("/api/actions/secret-submit")
    async def submit_secret(request: Request) -> JSONResponse:
        """AG-UI action endpoint for submitting masked credentials to local broker (UX-06)."""
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
                status_code=400, detail={"error": "Invalid secret payload"}
            ) from exc

        try:
            sec_in = SecretSubmitInput.model_validate(raw_data)
            secret_val = sec_in.resolve_secret()
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail={"error": "Invalid secret payload"}
            ) from exc

        fp = active_credential_broker.store(
            key_name=sec_in.key_name,
            secret_value=secret_val,
            service_name=sec_in.service_name or "",
        )

        req_id = sec_in.request_id
        fut = pending_secrets.get(req_id)
        if fut and not fut.done():
            fut.set_result(
                {
                    "requestId": req_id,
                    "keyName": sec_in.key_name,
                    "fingerprint": fp,
                    "secret": secret_val,
                }
            )

        return JSONResponse(
            status_code=200,
            content={
                "status": "stored",
                "keyName": sec_in.key_name,
                "fingerprint": fp,
                "systemPill": CredentialBroker.format_system_pill(sec_in.key_name, fp),
            },
        )

    @app.get("/api/agents")
    async def get_agents(request: Request) -> JSONResponse:
        """List active named specialist agent personas for the sidebar (UX-02)."""
        _verify_auth(request)
        agents_data: list[dict[str, Any]] = []
        try:
            from yusufclaw.roles import get_role_catalog

            catalog = get_role_catalog()
            for r in catalog.list_roles():
                agents_data.append(
                    {
                        "agentId": r.role_id,
                        "name": r.name,
                        "role": r.role_id,
                        "model": f"Tier {r.model_tier.capitalize()}",
                        "status": "online",
                        "description": r.description,
                        "allowedTools": r.allowed_tools,
                    }
                )
        except Exception:
            agents_data = [
                {
                    "agentId": "orchestrator",
                    "name": "YusufClaw Core",
                    "role": "orchestrator",
                    "model": "Strong (Claude 3.7 Sonnet)",
                    "status": "online",
                    "description": "General coordinator & conversationalist",
                    "allowedTools": ["web_fetch", "memory_search"],
                },
                {
                    "agentId": "coder",
                    "name": "Coder (Codex)",
                    "role": "coder",
                    "model": "Strong (Bedrock Sonnet)",
                    "status": "online",
                    "description": "Sandboxed software engineer",
                    "allowedTools": ["file_read", "file_write", "shell"],
                },
                {
                    "agentId": "researcher",
                    "name": "Researcher",
                    "role": "researcher",
                    "model": "Strong",
                    "status": "online",
                    "description": "Read-only web explorer & literature searcher",
                    "allowedTools": ["web_fetch", "arxiv_search"],
                },
                {
                    "agentId": "ops",
                    "name": "DevSecOps",
                    "role": "ops",
                    "model": "Strong",
                    "status": "online",
                    "description": "Cloud infra validator & security linter",
                    "allowedTools": ["shell", "infra_validate"],
                },
            ]
        return JSONResponse(status_code=200, content=agents_data)

    @app.get("/api/credentials")
    async def get_credentials(request: Request) -> JSONResponse:
        """List safe metadata for all stored credentials (fingerprints only, never plaintext) (UX-06)."""
        _verify_auth(request)
        creds = [
            s.model_dump(by_alias=True)
            for s in active_credential_broker.list_secrets()
        ]
        return JSONResponse(status_code=200, content=creds)

    def _memory_scope(agent_id: str, conversation_id: str) -> str:
        """Build the composite web session scope matching chat isolation (UX-02/UX-07)."""
        safe_agent = (agent_id or "orchestrator").strip() or "orchestrator"
        safe_conv = (conversation_id or "default").strip() or "default"
        if len(safe_agent) > 64 or len(safe_conv) > 128:
            raise HTTPException(status_code=400, detail={"error": "Invalid memory scope"})
        return f"web:{user_id}:{safe_agent}:{safe_conv}"

    def _serialize_memory_record(record: Any) -> dict[str, Any]:
        """Serialize a MemoryRecord for the inspection canvas without embeddings."""
        if hasattr(record, "model_dump"):
            dumped = record.model_dump(by_alias=True, exclude={"embedding"})
            return dict(dumped) if isinstance(dumped, dict) else {"record": dumped}
        if isinstance(record, dict):
            return {str(k): v for k, v in record.items() if k != "embedding"}
        return {"id": str(getattr(record, "id", "")), "text": str(record)}

    async def _list_memory_kind(
        scope: str, kind: MemoryKind | None, limit: int
    ) -> list[dict[str, Any]]:
        if memory is None or not hasattr(memory, "list"):
            return []
        opts = ListOptions(kind=kind, status="active", limit=limit)
        rows = await resolve_memory(memory.list(scope, opts))
        return [_serialize_memory_record(r) for r in rows]

    @app.get("/api/memory")
    async def get_memory_canvas(request: Request) -> JSONResponse:
        """Split-pane memory inspection payload: profile, facts, and episodic journal (UX-07)."""
        _verify_auth(request)
        agent_id = (request.query_params.get("agentId") or "orchestrator").strip()
        conversation_id = (
            request.query_params.get("conversationId")
            or request.query_params.get("session")
            or "default"
        ).strip()
        try:
            limit = int(request.query_params.get("limit") or "50")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": "Invalid limit"}) from exc
        limit = max(1, min(limit, 200))
        scope = _memory_scope(agent_id, conversation_id)

        if memory is None:
            return JSONResponse(
                status_code=200,
                content={
                    "scope": scope,
                    "agentId": agent_id,
                    "conversationId": conversation_id,
                    "profile": [],
                    "facts": [],
                    "journal": [],
                    "eventType": "memory.canvas",
                },
            )

        profile = await _list_memory_kind(scope, "profile", limit)
        facts = await _list_memory_kind(scope, "fact", limit)
        journal = await _list_memory_kind(scope, "note", limit)
        return JSONResponse(
            status_code=200,
            content={
                "scope": scope,
                "agentId": agent_id,
                "conversationId": conversation_id,
                "profile": profile,
                "facts": facts,
                "journal": journal,
                "eventType": "memory.canvas",
            },
        )

    @app.post("/api/memory/forget")
    async def forget_memory_record(request: Request) -> JSONResponse:
        """Deterministic unlearning: tombstone, FTS purge, drop from active retrieval (UX-07)."""
        _verify_auth(request)
        if memory is None or not hasattr(memory, "forget"):
            raise HTTPException(status_code=503, detail={"error": "Memory store unavailable"})

        content_type = request.headers.get("content-type", "").split(";")[0].strip()
        if content_type != "application/json":
            raise HTTPException(status_code=415, detail={"error": "JSON required"})
        body = await request.body()
        if len(body) > 100_000:
            raise HTTPException(status_code=413, detail={"error": "Request too large"})
        try:
            raw_data = json.loads(body.decode("utf-8"))
            forget_in = MemoryForgetInput.model_validate(raw_data)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail={"error": "Invalid forget payload"}
            ) from exc

        scope = _memory_scope(forget_in.agent_id, forget_in.conversation_id)
        record_id = forget_in.record_id.strip()
        existing = None
        if hasattr(memory, "get"):
            existing = await resolve_memory(
                memory.get(scope, record_id, include_inactive=True)
            )

        forgotten = await resolve_memory(
            memory.forget(scope, record_id, reason=forget_in.reason)
        )
        if not forgotten:
            raise HTTPException(
                status_code=404,
                detail={"error": "Memory record not found or already deleted"},
            )

        deleted_at = datetime.now(UTC).isoformat()
        if existing is not None and getattr(existing, "deleted_at", None):
            deleted_at = existing.deleted_at or deleted_at
        elif existing is not None and hasattr(existing, "deletedAt"):
            deleted_at = existing.deletedAt or deleted_at

        # Re-read after forget for accurate tombstone timestamp when available.
        if hasattr(memory, "get"):
            after = await resolve_memory(
                memory.get(scope, record_id, include_inactive=True)
            )
            if after is not None:
                deleted_at = getattr(after, "deleted_at", None) or getattr(
                    after, "deletedAt", None
                ) or deleted_at

        payload = {
            "recordId": record_id,
            "scope": scope,
            "deletedAt": deleted_at,
            "reason": forget_in.reason,
            "message": (
                "Fact deleted from active retrieval, FTS indices, and future context assemblies."
            ),
            "eventType": "memory.record_deleted",
        }
        return JSONResponse(status_code=200, content=payload)

    @app.patch("/api/memory/{record_id}")
    async def revise_memory_record(request: Request, record_id: str) -> JSONResponse:
        """In-place edit of an active claim with optimistic concurrency (UX-07)."""
        _verify_auth(request)
        if memory is None or not hasattr(memory, "revise"):
            raise HTTPException(status_code=503, detail={"error": "Memory store unavailable"})

        content_type = request.headers.get("content-type", "").split(";")[0].strip()
        if content_type != "application/json":
            raise HTTPException(status_code=415, detail={"error": "JSON required"})
        body = await request.body()
        if len(body) > 100_000:
            raise HTTPException(status_code=413, detail={"error": "Request too large"})
        try:
            raw_data = json.loads(body.decode("utf-8"))
            revise_in = MemoryReviseApiInput.model_validate(raw_data)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail={"error": "Invalid revise payload"}
            ) from exc

        target_id = record_id.strip()
        if not target_id or len(target_id) > 128:
            raise HTTPException(status_code=400, detail={"error": "Invalid record id"})

        scope = _memory_scope(revise_in.agent_id, revise_in.conversation_id)
        previous_revision: int | None = None
        if hasattr(memory, "get"):
            before = await resolve_memory(memory.get(scope, target_id))
            if before is not None:
                previous_revision = int(getattr(before, "revision", 1))

        try:
            updated = await resolve_memory(
                memory.revise(
                    scope,
                    target_id,
                    ReviseInput(
                        text=revise_in.text.strip(),
                        category=revise_in.category,
                        expected_revision=revise_in.expected_revision,
                        reason=revise_in.reason or "web memory canvas edit",
                    ),
                )
            )
        except ValueError as exc:
            msg = str(exc)
            status = 409 if "Version conflict" in msg else 404
            raise HTTPException(status_code=status, detail={"error": msg}) from exc

        return JSONResponse(
            status_code=200,
            content={
                "record": _serialize_memory_record(updated),
                "previousRevision": previous_revision,
                "eventType": "memory.record_updated",
            },
        )

    @app.get("/api/automation/routines")
    async def list_automation_routines(request: Request) -> JSONResponse:
        """List validated routine definitions with next-due timestamps (UX-08)."""
        _verify_auth(request)
        defs = await automation_store.list_definitions()
        return JSONResponse(
            status_code=200,
            content={
                "protocolVersion": AG_UI_PROTOCOL_VERSION,
                "routines": defs,
                "eventType": "routine.catalog",
            },
        )

    @app.post("/api/automation/routines")
    async def create_automation_routine(request: Request) -> JSONResponse:
        """Create a routine with strict grants and verified next-due (UX-08)."""
        _verify_auth(request)
        content_type = request.headers.get("content-type", "").split(";")[0].strip()
        if content_type != "application/json":
            raise HTTPException(status_code=415, detail={"error": "JSON required"})
        try:
            raw = json.loads((await request.body()).decode("utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("object required")
            payload = validate_routine_payload(raw)
            created = await automation_store.create(**payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail={"error": "Invalid routine payload"}
            ) from exc
        return JSONResponse(status_code=201, content=created)

    @app.delete("/api/automation/routines/{routine_id}")
    async def delete_automation_routine(
        request: Request, routine_id: str
    ) -> JSONResponse:
        """Delete a user-authored routine definition (UX-08)."""
        _verify_auth(request)
        removed = await automation_store.delete(routine_id)
        if not removed:
            raise HTTPException(status_code=404, detail={"error": "Routine not found"})
        return JSONResponse(
            status_code=200,
            content={"id": routine_id, "deleted": True, "eventType": "routine.deleted"},
        )

    @app.get("/api/connectors")
    async def list_connectors(request: Request) -> JSONResponse:
        """MCP connector hub status with pin/quarantine metadata (UX-11)."""
        _verify_auth(request)
        rows = serialize_connectors(active_mcp_config, active_pinning)
        return JSONResponse(
            status_code=200,
            content={
                "protocolVersion": "mcp/2025-03-26",
                "connectors": rows,
                "eventType": "connector.catalog",
            },
        )

    @app.post("/api/connectors/{connector_id}/pin")
    async def pin_connector(request: Request, connector_id: str) -> JSONResponse:
        """Approve current tool hash and clear quarantine (UX-11)."""
        _verify_auth(request)
        name = connector_id.strip()
        server = active_mcp_config.servers.get(name)
        if server is None:
            raise HTTPException(status_code=404, detail={"error": "Unknown connector"})
        body: dict[str, Any] = {}
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                raw = json.loads((await request.body()).decode("utf-8") or "{}")
                if isinstance(raw, dict):
                    body = raw
            except Exception:
                body = {}
        tools_hash = str(
            body.get("toolsHash") or body.get("pinnedHash") or server.pinned_hash or ""
        ).strip()
        if not tools_hash:
            raise HTTPException(
                status_code=400,
                detail={"error": "toolsHash required to pin connector"},
            )
        rec = active_pinning.pin_server(
            name, tools_hash=tools_hash, version=server.version
        )
        server.quarantined = False
        server.pinned_hash = tools_hash
        return JSONResponse(
            status_code=200,
            content={
                "connectorId": name,
                "status": "connected",
                "pinnedHash": rec.tools_hash,
                "protocolVersion": server.protocol_version,
                "eventType": "connector.status_changed",
            },
        )

    @app.post("/api/connectors/{connector_id}/quarantine")
    async def quarantine_connector(request: Request, connector_id: str) -> JSONResponse:
        """Force-quarantine a connector until re-pin (UX-11)."""
        _verify_auth(request)
        name = connector_id.strip()
        server = active_mcp_config.servers.get(name)
        if server is None:
            raise HTTPException(status_code=404, detail={"error": "Unknown connector"})
        reason = "Operator quarantine"
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                raw = json.loads((await request.body()).decode("utf-8") or "{}")
                if isinstance(raw, dict) and raw.get("reason"):
                    reason = str(raw["reason"])[:512]
            except Exception:
                pass
        rec = active_pinning.quarantine_server(
            name,
            reason=reason,
            tools_hash=server.pinned_hash or "",
            version=server.version,
        )
        server.quarantined = True
        return JSONResponse(
            status_code=200,
            content={
                "connectorId": name,
                "status": "quarantined",
                "quarantineReason": rec.quarantine_reason,
                "pinnedHash": rec.tools_hash,
                "eventType": "connector.status_changed",
            },
        )

    @app.get("/api/teammates")
    async def list_teammates(request: Request) -> JSONResponse:
        """A2A teammate hierarchy visualizer snapshot (UX-12)."""
        _verify_auth(request)
        return JSONResponse(
            status_code=200,
            content=serialize_teammates(active_teammate_registry, active_teammate_hook),
        )

    @app.get("/api/usage")
    async def get_usage_meter(request: Request) -> JSONResponse:
        """Token/cost meter against the $50 monthly ceiling (UX-13)."""
        _verify_auth(request)
        await active_usage.load_monthly()
        return JSONResponse(status_code=200, content=active_usage.snapshot())

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
                    pending_questions=pending_questions,
                    pending_secrets=pending_secrets,
                    credential_broker=active_credential_broker,
                    usage_meter=active_usage,
                    teammate_hook=active_teammate_hook,
                ),
                media_type="text/event-stream",
            )

        # Standard synchronous turn
        try:
            active_usage.begin_turn()
            reply = await actual_bus.dispatch(
                IncomingMessage(
                    channel="web",
                    userId=user_id,
                    conversationId=msg_in.conversation_id,
                    requestId=msg_in.request_id,
                    text=msg_in.text,
                    agentId=msg_in.agentId,
                )
            )
            approx_prompt = max(1, len(msg_in.text) // 4)
            approx_completion = max(1, len(reply or "") // 4)
            await active_usage.record_tokens(approx_prompt, approx_completion)
            return JSONResponse(
                status_code=200,
                content={"reply": reply, "usage": active_usage.snapshot()},
            )
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
                pending_questions=pending_questions,
                pending_secrets=pending_secrets,
                credential_broker=active_credential_broker,
                usage_meter=active_usage,
                teammate_hook=active_teammate_hook,
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
    pending_questions: dict[str, dict[str, Any]] | None = None,
    pending_secrets: dict[str, asyncio.Future[dict[str, Any]]] | None = None,
    credential_broker: CredentialBroker | None = None,
    usage_meter: UsageMeter | None = None,
    teammate_hook: TeammateStatusHook | None = None,
) -> AsyncIterator[str]:
    """Emit AG-UI 1.0 Server-Sent Events (run.acknowledged, run.milestone, message.delta, widgets, run.completed)."""
    run_id = f"run-{uuid.uuid4()}"
    msg_id = f"msg-{uuid.uuid4()}"
    event_queue: asyncio.Queue[str | None] = asyncio.Queue()
    emitted_text_start = False
    emitted_any_delta = False
    session_id = f"web:{user_id}:{msg_in.agentId}:{msg_in.conversation_id}"
    agent_id = msg_in.agentId
    sequence = 0
    start_time = datetime.now(UTC)
    accumulated_text = ""
    current_step = 1
    meter = usage_meter or UsageMeter()
    meter.begin_turn()
    hook_seen = 0
    if teammate_hook is not None:
        hook_seen = len(teammate_hook.history)

    def agui_ev(event_type: str, payload: dict[str, Any]) -> str:
        nonlocal sequence
        sequence += 1
        return format_agui_event(
            event_type=event_type,
            payload=payload,
            run_id=run_id,
            session_id=session_id,
            agent_id=agent_id,
            sequence=sequence,
        )

    # 1. Immediate cognitive acknowledgment (<300ms, UX-03)
    yield agui_ev(
        "run.acknowledged",
        {
            "runId": run_id,
            "intent": msg_in.text[:120],
            "estimatedSteps": 2,
        },
    )

    # 2. Initial milestone stepper event (UX-03)
    yield agui_ev(
        "run.milestone",
        {
            "step": 1,
            "totalSteps": 2,
            "title": "Analyzing task requirements",
            "status": "running",
            "detail": msg_in.text[:120],
            "durationMs": 0,
        },
    )

    # 3. Legacy RunStarted and progress events for backward compatibility
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
        nonlocal emitted_text_start, emitted_any_delta, current_step, accumulated_text
        if event_name == "tool_call_start":
            current_step += 1
            call_id = payload.get("id") or str(uuid.uuid4())
            tool_name = payload.get("name", "")
            clean_args = sanitize_args_summary(payload.get("args", {}))
            elapsed = int((datetime.now(UTC) - start_time).total_seconds() * 1000)
            await event_queue.put(
                agui_ev(
                    "run.milestone",
                    {
                        "step": current_step,
                        "totalSteps": current_step + 1,
                        "title": f"Executing tool: {tool_name}",
                        "status": "running",
                        "detail": f"Calling {tool_name}",
                        "durationMs": elapsed,
                    },
                )
            )
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
            elapsed = int((datetime.now(UTC) - start_time).total_seconds() * 1000)
            await event_queue.put(
                agui_ev(
                    "run.milestone",
                    {
                        "step": current_step,
                        "totalSteps": current_step + 1,
                        "title": f"Completed tool: {tool_name}",
                        "status": "completed" if status == "success" else "failed",
                        "detail": f"Status: {status}",
                        "durationMs": elapsed,
                    },
                )
            )
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
                accumulated_text += delta
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
                    agui_ev(
                        "message.delta",
                        {
                            "delta": delta,
                            "accumulated": accumulated_text,
                            "messageId": msg_id,
                            "requestId": msg_in.request_id,
                            "conversationId": msg_in.conversation_id,
                        },
                    )
                )
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
        elif event_name == "thought":
            thought_text = str(payload.get("thought", ""))
            if thought_text:
                await event_queue.put(
                    agui_ev("message.thought", {"thought": thought_text})
                )
        elif event_name in ("ask_question", "widget.question"):
            await web_ask_question(payload)
        elif event_name in ("secret_request", "widget.secret_request"):
            await web_request_secret(payload)

    async def web_ask_question(
        question_payload: dict[str, Any] | QuestionWidget,
    ) -> dict[str, Any]:
        if isinstance(question_payload, dict):
            q = QuestionWidget.model_validate(question_payload)
        else:
            q = question_payload
        q_dict = q.model_dump(by_alias=True)
        q_dict["requestId"] = msg_in.request_id
        q_dict["conversationId"] = msg_in.conversation_id
        q_dict["agentId"] = msg_in.agentId

        await event_queue.put(agui_ev("widget.question", q_dict))

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        if pending_questions is not None:
            pending_questions[q.question_id] = {
                "future": fut,
                "question": q,
            }

        timeout_sec = (q.timeout_ms or 300_000) / 1000.0
        try:
            return await asyncio.wait_for(fut, timeout=timeout_sec)
        except (TimeoutError, asyncio.CancelledError):
            return {"error": "timeout", "selectedOptionIds": [], "customText": None}
        finally:
            if pending_questions is not None:
                pending_questions.pop(q.question_id, None)

    async def web_request_secret(
        req_payload: dict[str, Any] | SecretRequestWidget,
    ) -> dict[str, Any]:
        if isinstance(req_payload, dict):
            sr = SecretRequestWidget.model_validate(req_payload)
        else:
            sr = req_payload
        sr_dict = sr.model_dump(by_alias=True)
        sr_dict["requestId"] = msg_in.request_id
        sr_dict["conversationId"] = msg_in.conversation_id
        sr_dict["agentId"] = msg_in.agentId

        await event_queue.put(agui_ev("widget.secret_request", sr_dict))

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        if pending_secrets is not None:
            pending_secrets[sr.request_id] = fut

        try:
            return await asyncio.wait_for(fut, timeout=300.0)
        except (TimeoutError, asyncio.CancelledError):
            return {"error": "timeout", "keyName": sr.key_name}
        finally:
            if pending_secrets is not None:
                pending_secrets.pop(sr.request_id, None)

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
        card_dict["agentId"] = msg_in.agentId

        # AG-UI 1.0 typed approval widget event
        await event_queue.put(agui_ev("widget.approval_card", card_dict))
        # Legacy compatibility events
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

        async def _watch_provider() -> None:
            """Watch approval provider for resolution via Telegram or CLI."""
            while not fut.done():
                if approval_provider is not None:
                    rec = approval_provider.get(app_id)
                    if rec:
                        if rec.status in ("approved", "consumed"):
                            if not fut.done():
                                fut.set_result(True)
                            break
                        elif rec.status in ("denied", "expired"):
                            if not fut.done():
                                fut.set_result(False)
                            break
                await asyncio.sleep(0.05)

        watcher_task = asyncio.create_task(_watch_provider())
        try:
            return await asyncio.wait_for(fut, timeout=300.0)
        except (TimeoutError, asyncio.CancelledError):
            return False
        finally:
            if not watcher_task.done():
                watcher_task.cancel()
                try:
                    await watcher_task
                except asyncio.CancelledError:
                    pass
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
                        agentId=msg_in.agentId,
                    ),
                    approve=web_approve,
                    ask_question=web_ask_question,
                )
            finally:
                reset_turn_event_hook(token)

            elapsed_ms = int((datetime.now(UTC) - start_time).total_seconds() * 1000)

            # Final milestone (UX-03)
            await event_queue.put(
                agui_ev(
                    "run.milestone",
                    {
                        "step": current_step + 1,
                        "totalSteps": current_step + 1,
                        "title": "Turn response completed",
                        "status": "completed",
                        "detail": "Synthesized final model response",
                        "durationMs": elapsed_ms,
                    },
                )
            )

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
                    agui_ev(
                        "message.delta",
                        {
                            "delta": reply,
                            "accumulated": reply,
                            "messageId": msg_id,
                            "requestId": msg_in.request_id,
                            "conversationId": msg_in.conversation_id,
                        },
                    )
                )
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
            # AG-UI run.completed event (UX-01)
            await event_queue.put(
                agui_ev(
                    "run.completed",
                    {
                        "runId": run_id,
                        "status": "succeeded",
                        "summary": reply[:200],
                        "totalDurationMs": elapsed_ms,
                    },
                )
            )
            # UX-12: flush teammate hierarchy events observed during the turn
            if teammate_hook is not None:
                for ev in teammate_hook.history[hook_seen:]:
                    await event_queue.put(
                        agui_ev(
                            ev.event_type,
                            ev.model_dump(by_alias=True, mode="json"),
                        )
                    )
            # UX-13: usage meter against $50 monthly ceiling
            approx_prompt = max(1, len(msg_in.text) // 4)
            approx_completion = max(1, len(reply or accumulated_text) // 4)
            await meter.record_tokens(approx_prompt, approx_completion)
            await event_queue.put(agui_ev("meter.usage", meter.snapshot()))
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
            elapsed_ms = int((datetime.now(UTC) - start_time).total_seconds() * 1000)
            err_msg = "Request failed. Check provider configuration and connectivity."
            await event_queue.put(
                agui_ev(
                    "run.milestone",
                    {
                        "step": current_step,
                        "totalSteps": current_step,
                        "title": "Turn failed",
                        "status": "failed",
                        "detail": err_msg,
                        "durationMs": elapsed_ms,
                    },
                )
            )
            await event_queue.put(
                agui_ev(
                    "run.completed",
                    {
                        "runId": run_id,
                        "status": "failed",
                        "summary": err_msg,
                        "totalDurationMs": elapsed_ms,
                    },
                )
            )
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
        credential_broker: CredentialBroker | None = None,
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
        self.credential_broker = credential_broker
        self.app = create_app(
            bus=bus,
            memory=memory,
            token=opts.token,
            user_id=opts.user_id,
            approval_provider=approval_provider,
            credential_broker=credential_broker,
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
    "AgUiEnvelope",
    "AgentPersona",
    "ApprovalInput",
    "ChatMessageInput",
    "HealthResponse",
    "InboxItem",
    "MilestoneStep",
    "QuestionOption",
    "QuestionWidget",
    "RoutineTarget",
    "SecretRequestWidget",
    "SecretSubmitInput",
    "ServerAddress",
    "ServerWrapper",
    "WebAdapter",
    "WebAdapterOptions",
    "WidgetResolveInput",
    "app",
    "create_app",
    "format_agui_event",
    "format_sse_event",
    "sanitize_args_summary",
    "sanitize_result_summary",
    "sanitize_secret_patterns",
]
