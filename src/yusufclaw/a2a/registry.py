"""Teammate registry for Agent-to-Agent (A2A 1.0) local coordination.

Enforces:
- Depth-1 governance: Tracks depth-1 children only; strictly prohibits grandchildren.
- Concurrency ceilings: Enforces max concurrent workers per parent (default: 3).
- Budget caps per child: Merges and clamps task budget against AgentCard limits.
- Cancellation propagation: Cancelling a parent run cancels all child teammate tasks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from yusufclaw.a2a.models import AgentCard, TaskRequest, TaskResult, TeammateBudget
from yusufclaw.a2a.transport import A2ATransport, HttpJsonTransport, InProcessTransport

logger = logging.getLogger(__name__)


class DepthLimitExceededError(PermissionError):
    """Raised when delegation depth exceeds the architectural depth-1 boundary."""


class TeammateNotFoundError(KeyError):
    """Raised when dispatching to an unregistered teammate identifier."""


class ConcurrencyLimitExceededError(RuntimeError):
    """Raised when active subagent count exceeds max concurrent workers."""


@dataclass
class ActiveTeammateTask:
    """Tracking record for an in-flight child teammate execution."""

    task_id: str
    parent_task_id: str | None
    caller_id: str
    agent_id: str
    depth: int
    started_at: float
    budget: TeammateBudget
    transport: A2ATransport
    status: str = "running"
    async_task: asyncio.Task[TaskResult] | None = None


class TeammateRegistry:
    """Registry managing local teammates, enforcing depth-1 limits and cancellation."""

    def __init__(
        self,
        max_concurrent_workers: int = 3,
        default_transport: A2ATransport | None = None,
    ) -> None:
        self.max_concurrent_workers = max_concurrent_workers
        self.default_transport = default_transport or InProcessTransport()
        self._cards: dict[str, AgentCard] = {}
        self._transports: dict[str, A2ATransport] = {}
        self._active_tasks: dict[str, ActiveTeammateTask] = {}
        self._parent_to_children: dict[str, set[str]] = {}

    def register(
        self,
        card: AgentCard,
        transport: A2ATransport | None = None,
    ) -> None:
        """Register an AgentCard and optionally bind a specific transport."""
        self._cards[card.id] = card
        if transport is not None:
            self._transports[card.id] = transport
        elif card.endpoint:
            self._transports[card.id] = HttpJsonTransport(base_url=card.endpoint)

    def unregister(self, agent_id: str) -> None:
        """Remove a teammate from the registry."""
        self._cards.pop(agent_id, None)
        self._transports.pop(agent_id, None)

    def get(self, agent_id: str) -> AgentCard | None:
        """Retrieve an AgentCard by teammate ID or matching role."""
        if agent_id in self._cards:
            return self._cards[agent_id]
        # Secondary fallback: search by role
        for card in self._cards.values():
            if card.role == agent_id:
                return card
        return None

    def get_transport(self, agent_id: str) -> A2ATransport:
        """Resolve the transport bound to the given agent ID."""
        return self._transports.get(agent_id, self.default_transport)

    def list_teammates(self) -> list[AgentCard]:
        """List all currently registered teammate cards."""
        return list(self._cards.values())

    def list_active_tasks(self) -> list[ActiveTeammateTask]:
        """List all currently in-flight child tasks."""
        return list(self._active_tasks.values())

    def can_spawn(
        self,
        depth: int,
        parent_task_id: str | None = None,
        caller_id: str | None = None,
    ) -> tuple[bool, str]:
        """Verify depth-1 governance and concurrency ceilings."""
        if depth > 1:
            return False, f"Recursion depth {depth} exceeds maximum allowed depth of 1"

        if parent_task_id and parent_task_id in self._active_tasks:
            parent_task = self._active_tasks[parent_task_id]
            if parent_task.depth >= 1:
                return (
                    False,
                    "Caller is already a child subagent; grandchildren are strictly prohibited (depth-1 limit)",
                )

        # Concurrency check
        if parent_task_id:
            active_children = self._parent_to_children.get(parent_task_id, set())
            if len(active_children) >= self.max_concurrent_workers:
                return (
                    False,
                    f"Concurrency ceiling reached: parent '{parent_task_id}' already has "
                    f"{len(active_children)} active workers (max {self.max_concurrent_workers})",
                )

        return True, "OK"

    async def dispatch_task(self, request: TaskRequest) -> TaskResult:
        """Dispatch a task to a registered teammate with depth-1 & budget checks."""
        allowed, reason = self.can_spawn(
            depth=request.depth,
            parent_task_id=request.parent_task_id,
            caller_id=request.caller_id,
        )
        if not allowed:
            if "depth" in reason.lower():
                raise DepthLimitExceededError(reason)
            raise ConcurrencyLimitExceededError(reason)

        card = self.get(request.target_agent_id)
        if card is None:
            raise TeammateNotFoundError(
                f"Teammate '{request.target_agent_id}' is not registered in TeammateRegistry"
            )

        # Merge and clamp budget: child task cannot exceed card's configured limits
        effective_budget = request.budget.model_copy()
        if card.budget.max_tokens > 0:
            if effective_budget.max_tokens <= 0:
                effective_budget.max_tokens = card.budget.max_tokens
            else:
                effective_budget.max_tokens = min(
                    effective_budget.max_tokens, card.budget.max_tokens
                )
        if card.budget.timeout_seconds > 0:
            if effective_budget.timeout_seconds <= 0:
                effective_budget.timeout_seconds = card.budget.timeout_seconds
            else:
                effective_budget.timeout_seconds = min(
                    effective_budget.timeout_seconds, card.budget.timeout_seconds
                )
        if card.budget.max_steps > 0:
            if effective_budget.max_steps <= 0:
                effective_budget.max_steps = card.budget.max_steps
            else:
                effective_budget.max_steps = min(
                    effective_budget.max_steps, card.budget.max_steps
                )

        bounded_request = request.model_copy(update={"budget": effective_budget})
        transport = self.get_transport(card.id)

        # Register active task
        active_record = ActiveTeammateTask(
            task_id=request.task_id,
            parent_task_id=request.parent_task_id,
            caller_id=request.caller_id,
            agent_id=card.id,
            depth=request.depth,
            started_at=time.perf_counter(),
            budget=effective_budget,
            transport=transport,
        )
        self._active_tasks[request.task_id] = active_record
        if request.parent_task_id:
            self._parent_to_children.setdefault(request.parent_task_id, set()).add(
                request.task_id
            )

        try:
            result = await transport.send_task(bounded_request)
            return result
        except asyncio.CancelledError:
            await transport.cancel_task(request.task_id)
            return TaskResult(
                task_id=request.task_id,
                agent_id=card.id,
                status="cancelled",
                error="Task cancelled during execution",
            )
        finally:
            self._active_tasks.pop(request.task_id, None)
            if request.parent_task_id and request.parent_task_id in self._parent_to_children:
                self._parent_to_children[request.parent_task_id].discard(request.task_id)
                if not self._parent_to_children[request.parent_task_id]:
                    self._parent_to_children.pop(request.parent_task_id, None)

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel an individual active child task."""
        record = self._active_tasks.get(task_id)
        if record is None:
            return False

        record.status = "cancelling"
        success = await record.transport.cancel_task(task_id)
        if record.async_task and not record.async_task.done():
            record.async_task.cancel()
        return success

    async def cancel_parent(self, parent_task_id: str) -> int:
        """Propagate cancellation from a parent run to all active child tasks."""
        children = set(self._parent_to_children.get(parent_task_id, set()))
        cancelled_count = 0
        for child_task_id in children:
            ok = await self.cancel_task(child_task_id)
            if ok:
                cancelled_count += 1
        return cancelled_count

    async def cancel_all(self) -> int:
        """Cancel all currently active child tasks across the registry."""
        task_ids = list(self._active_tasks.keys())
        cancelled_count = 0
        for tid in task_ids:
            if await self.cancel_task(tid):
                cancelled_count += 1
        return cancelled_count
