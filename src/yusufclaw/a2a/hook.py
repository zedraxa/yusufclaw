"""Thin event hook publishing teammate status events for AG-UI UX-12.

Publishes:
- teammate.spawned: Subagent spawn notification with role, tools, and budget ceiling.
- teammate.step: Live step progress with tokens consumed and step summary.
- teammate.finished: Subagent completion outcome, artifact reference, and USD cost.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable

from yusufclaw.a2a.events import TeammateEvent

logger = logging.getLogger(__name__)

TeammateListener = Callable[[TeammateEvent], Awaitable[None] | None]


class TeammateStatusHook:
    """Manages listeners and publishes teammate status events."""

    def __init__(self, max_history: int = 200) -> None:
        self._listeners: list[TeammateListener] = []
        self._history: list[TeammateEvent] = []
        self._max_history = max_history

    def add_listener(self, listener: TeammateListener) -> None:
        """Register a callback for teammate events."""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: TeammateListener) -> None:
        """Unregister a callback."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    @property
    def history(self) -> list[TeammateEvent]:
        """Return a copy of the event history."""
        return list(self._history)

    def clear_history(self) -> None:
        """Clear recorded events."""
        self._history.clear()

    async def emit(self, event: TeammateEvent) -> None:
        """Record and broadcast an event to all registered listeners."""
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history.pop(0)

        for listener in list(self._listeners):
            try:
                res = listener(event)
                if inspect.isawaitable(res):
                    await res
            except Exception as e:
                logger.warning("Error in teammate event listener: %s", e)

    async def emit_spawned(
        self,
        subagent_id: str,
        parent_run_id: str | None = None,
        role: str | None = None,
        task: str | None = None,
        tools: list[str] | None = None,
        max_cost: float | None = None,
    ) -> TeammateEvent:
        """Emit a teammate.spawned event."""
        event = TeammateEvent(
            event_type="teammate.spawned",
            subagent_id=subagent_id,
            parent_run_id=parent_run_id,
            role=role,
            task=task,
            tools=tools or [],
            max_cost=max_cost,
        )
        await self.emit(event)
        return event

    async def emit_step(
        self,
        subagent_id: str,
        step: int,
        summary: str | None = None,
        tokens: int | None = None,
    ) -> TeammateEvent:
        """Emit a teammate.step event."""
        event = TeammateEvent(
            event_type="teammate.step",
            subagent_id=subagent_id,
            step=step,
            summary=summary,
            tokens=tokens,
        )
        await self.emit(event)
        return event

    async def emit_finished(
        self,
        subagent_id: str,
        status: str,
        artifact_ref: str | None = None,
        cost_usd: float | None = None,
    ) -> TeammateEvent:
        """Emit a teammate.finished event."""
        event = TeammateEvent(
            event_type="teammate.finished",
            subagent_id=subagent_id,
            status=status,
            artifact_ref=artifact_ref,
            cost_usd=cost_usd,
        )
        await self.emit(event)
        return event
