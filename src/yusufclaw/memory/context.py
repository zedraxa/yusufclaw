"""Session context management using Python contextvars."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class ActiveSessionContext:
    """Current active session and request metadata."""

    session: str
    request_id: str


active_session_context: ContextVar[ActiveSessionContext | None] = ContextVar(
    "active_session_context", default=None
)


@contextmanager
def session_scope(
    session: str, request_id: str
) -> Generator[ActiveSessionContext, None, None]:
    """Context manager to safely activate a session context for a block."""
    ctx = ActiveSessionContext(session=session, request_id=request_id)
    token = active_session_context.set(ctx)
    try:
        yield ctx
    finally:
        active_session_context.reset(token)


__all__ = ["ActiveSessionContext", "active_session_context", "session_scope"]
