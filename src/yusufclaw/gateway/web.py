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
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from yusufclaw import __version__
from yusufclaw.config import Settings, get_settings
from yusufclaw.gateway.bus import IncomingMessage, MessageBus
from yusufclaw.gateway.web_page import CHAT_PAGE, CHAT_SCRIPT, CHAT_STYLE
from yusufclaw.telemetry import setup_telemetry


class HealthResponse(BaseModel):
    """Health check response schema."""

    status: str
    version: str
    service: str
    model_provider: str


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
                _stream_chat_events(actual_bus, user_id, msg_in),
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
            _stream_chat_events(actual_bus, user_id, msg_in),
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

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    return app


async def _stream_chat_events(
    bus: MessageBus, user_id: str, msg_in: ChatMessageInput
) -> AsyncIterator[str]:
    """Emit AG-UI 1.0 style Server-Sent Events (progress, tool calls, message, done)."""
    yield format_sse_event(
        "progress",
        {"status": "thinking", "step": 1, "requestId": msg_in.request_id},
    )

    try:
        reply = await bus.dispatch(
            IncomingMessage(
                channel="web",
                userId=user_id,
                conversationId=msg_in.conversation_id,
                requestId=msg_in.request_id,
                text=msg_in.text,
            )
        )
        yield format_sse_event(
            "message",
            {"role": "assistant", "reply": reply, "requestId": msg_in.request_id},
        )
        yield format_sse_event(
            "done",
            {"status": "completed", "requestId": msg_in.request_id},
        )
    except Exception as exc:
        yield format_sse_event(
            "error",
            {
                "error": "Request failed. Check provider configuration and connectivity.",
                "detail": str(exc),
            },
        )


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
        self.app = create_app(
            bus=bus,
            memory=memory,
            token=opts.token,
            user_id=opts.user_id,
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
]
