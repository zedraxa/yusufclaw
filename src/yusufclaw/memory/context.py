"""Session context management using Python contextvars."""

import math
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime

from yusufclaw.memory.models import ContextAssemblyOptions, MemoryRecord


@dataclass(frozen=True)
class ActiveSessionContext:
    """Current active session and request metadata."""

    session: str
    request_id: str


active_session_context: ContextVar[ActiveSessionContext | None] = ContextVar(
    "active_session_context", default=None
)


@contextmanager
def session_scope(session: str, request_id: str) -> Generator[ActiveSessionContext, None, None]:
    """Context manager to safely activate a session context for a block."""
    ctx = ActiveSessionContext(session=session, request_id=request_id)
    token = active_session_context.set(ctx)
    try:
        yield ctx
    finally:
        active_session_context.reset(token)


__all__ = [
    "ActiveSessionContext",
    "active_session_context",
    "session_scope",
    "memory_score",
    "rank_memories",
]


def memory_score(
    record: MemoryRecord,
    *,
    relevance: float = 1.0,
    importance: float = 1.0,
    half_life_days: float = 90,
    pinned: bool = False,
    now: datetime | None = None,
) -> float:
    """Rank by relevance × importance × exponential age decay; never delete."""
    if half_life_days <= 0 or not math.isfinite(half_life_days):
        raise ValueError("half_life_days must be finite and positive")
    if not all(math.isfinite(v) and v >= 0 for v in (relevance, importance)):
        raise ValueError("relevance and importance must be finite and nonnegative")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    if record.status != "active":
        return -1.0

    def timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    try:
        if record.valid_to and timestamp(record.valid_to) <= current:
            return -1.0
        if record.valid_from and timestamp(record.valid_from) > current:
            return -1.0
        age = max(0.0, (current - timestamp(record.updated_at)).total_seconds() / 86400)
    except ValueError:
        age = half_life_days  # Unknown age must not get a freshness boost.
    return relevance * importance * (1.0 if pinned else 2 ** (-age / half_life_days))


def rank_memories(
    records: list[MemoryRecord],
    options: ContextAssemblyOptions,
) -> list[MemoryRecord]:
    """Rerank a bounded retrieval pool, using retrieval position as relevance."""
    now = datetime.now(UTC)
    scored = [
        (
            r,
            memory_score(
                r,
                relevance=1 / (1 + i / 10),
                importance=options.importance.get(r.id, 1.0),
                half_life_days=options.decay_half_life_days,
                pinned=r.id in options.pinned_ids,
                now=now,
            ),
        )
        for i, r in enumerate(records)
    ]
    scored = [(r, score) for r, score in scored if score >= 0]
    scored.sort(key=lambda item: (item[0].id in options.pinned_ids, item[1]), reverse=True)
    return [r for r, _ in scored]
