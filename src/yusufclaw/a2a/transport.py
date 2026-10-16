"""Transport implementations for Agent-to-Agent (A2A) communication.

Provides:
- A2ATransport: Abstract protocol for task dispatch and cancellation.
- InProcessTransport: Zero-overhead, offline in-memory transport with budget enforcement
  and cancellation propagation.
- HttpJsonTransport: Optional loopback HTTP transport for same-host decoupled agent processes.
- create_a2a_router: FastAPI APIRouter helper exposing local A2A endpoints.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

import httpx
from fastapi import APIRouter, HTTPException, status

from yusufclaw.a2a.models import AgentCard, TaskRequest, TaskResult

logger = logging.getLogger(__name__)

TaskHandler = Callable[[TaskRequest], Awaitable[TaskResult]]


@runtime_checkable
class A2ATransport(Protocol):
    """Transport protocol for A2A communication."""

    async def send_task(self, request: TaskRequest) -> TaskResult:
        """Dispatch a task request and await the result."""
        ...

    async def cancel_task(self, task_id: str) -> bool:
        """Request cancellation of an active task."""
        ...


class InProcessTransport:
    """In-process transport executing teammates within the same runtime.

    Enforces:
    - Per-child timeout constraints via asyncio.wait_for.
    - Tracking of active asyncio Tasks by task_id.
    - Cancellation propagation when parent or registry cancels a child.
    """

    def __init__(
        self,
        default_handler: TaskHandler | None = None,
    ) -> None:
        self.default_handler = default_handler
        self._handlers: dict[str, TaskHandler] = {}
        self._active_tasks: dict[str, asyncio.Task[TaskResult]] = {}

    def register_handler(self, agent_id: str, handler: TaskHandler) -> None:
        """Register a task execution handler for a specific teammate id."""
        self._handlers[agent_id] = handler

    def unregister_handler(self, agent_id: str) -> None:
        """Remove a task handler."""
        self._handlers.pop(agent_id, None)

    async def send_task(self, request: TaskRequest) -> TaskResult:
        """Execute a task request in-process with budget and timeout enforcement."""
        handler = self._handlers.get(request.target_agent_id) or self.default_handler
        if handler is None:
            return TaskResult(
                task_id=request.task_id,
                agent_id=request.target_agent_id,
                status="failed",
                error=f"No in-process handler registered for agent '{request.target_agent_id}'",
            )

        start_time = time.perf_counter()
        timeout = (
            request.budget.timeout_seconds
            if request.budget and request.budget.timeout_seconds > 0
            else None
        )

        async def _run_wrapper() -> TaskResult:
            try:
                res = await handler(request)
                res.duration_ms = (time.perf_counter() - start_time) * 1000.0
                return res
            except asyncio.CancelledError:
                return TaskResult(
                    task_id=request.task_id,
                    agent_id=request.target_agent_id,
                    status="cancelled",
                    error="Task was cancelled by parent or registry",
                    duration_ms=(time.perf_counter() - start_time) * 1000.0,
                )
            except Exception as e:
                logger.error("In-process task %s failed: %s", request.task_id, e)
                return TaskResult(
                    task_id=request.task_id,
                    agent_id=request.target_agent_id,
                    status="failed",
                    error=str(e),
                    duration_ms=(time.perf_counter() - start_time) * 1000.0,
                )

        exec_task = asyncio.create_task(_run_wrapper())
        self._active_tasks[request.task_id] = exec_task

        try:
            if timeout is not None:
                try:
                    result = await asyncio.wait_for(asyncio.shield(exec_task), timeout=timeout)
                except TimeoutError:
                    exec_task.cancel()
                    result = TaskResult(
                        task_id=request.task_id,
                        agent_id=request.target_agent_id,
                        status="timeout",
                        error=f"Task timed out after {timeout} seconds",
                        duration_ms=(time.perf_counter() - start_time) * 1000.0,
                    )
            else:
                result = await exec_task
        except asyncio.CancelledError:
            exec_task.cancel()
            result = TaskResult(
                task_id=request.task_id,
                agent_id=request.target_agent_id,
                status="cancelled",
                error="Task cancelled by caller",
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )
        finally:
            self._active_tasks.pop(request.task_id, None)

        return result

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel an active in-process task."""
        task = self._active_tasks.get(task_id)
        if task is not None and not task.done():
            task.cancel()
            return True
        return False


class HttpJsonTransport:
    """HTTP JSON transport for same-host decoupled agent teammates."""

    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        default_timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client
        self.default_timeout = default_timeout

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(timeout=self.default_timeout)

    async def send_task(self, request: TaskRequest) -> TaskResult:
        """Dispatch a task request over HTTP POST /a2a/tasks."""
        start_time = time.perf_counter()
        timeout = (
            float(request.budget.timeout_seconds)
            if request.budget and request.budget.timeout_seconds > 0
            else self.default_timeout
        )

        client = await self._get_client()
        owns_client = self._client is None

        payload = request.model_dump(mode="json")
        url = f"{self.base_url}/a2a/tasks"

        try:
            resp = await client.post(url, json=payload, timeout=timeout)
            if resp.status_code == 200:
                result = TaskResult.model_validate(resp.json())
                result.duration_ms = (time.perf_counter() - start_time) * 1000.0
                return result
            return TaskResult(
                task_id=request.task_id,
                agent_id=request.target_agent_id,
                status="failed",
                error=f"HTTP {resp.status_code}: {resp.text}",
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )
        except httpx.TimeoutException:
            return TaskResult(
                task_id=request.task_id,
                agent_id=request.target_agent_id,
                status="timeout",
                error=f"HTTP request timed out after {timeout}s",
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )
        except asyncio.CancelledError:
            # Propagate cancellation to remote host
            try:
                await client.post(f"{self.base_url}/a2a/tasks/{request.task_id}/cancel")
            except Exception:
                pass
            return TaskResult(
                task_id=request.task_id,
                agent_id=request.target_agent_id,
                status="cancelled",
                error="HTTP task was cancelled",
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )
        except Exception as e:
            return TaskResult(
                task_id=request.task_id,
                agent_id=request.target_agent_id,
                status="failed",
                error=f"HTTP transport connection error: {e}",
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )
        finally:
            if owns_client:
                await client.aclose()

    async def cancel_task(self, task_id: str) -> bool:
        """Notify remote teammate to cancel the task."""
        client = await self._get_client()
        owns_client = self._client is None
        try:
            resp = await client.post(f"{self.base_url}/a2a/tasks/{task_id}/cancel")
            return resp.status_code == 200
        except Exception as e:
            logger.warning("Failed to cancel HTTP task %s: %s", task_id, e)
            return False
        finally:
            if owns_client:
                await client.aclose()


def create_a2a_router(
    handler: TaskHandler,
    card: AgentCard | None = None,
    cancel_handler: Callable[[str], Awaitable[bool]] | None = None,
) -> APIRouter:
    """Create a FastAPI APIRouter exposing local A2A endpoints."""
    router = APIRouter(prefix="/a2a", tags=["A2A"])

    @router.post("/tasks", response_model=TaskResult)
    async def post_task(request: TaskRequest) -> TaskResult:
        return await handler(request)

    @router.post("/tasks/{task_id}/cancel")
    async def post_cancel(task_id: str) -> dict[str, Any]:
        cancelled = False
        if cancel_handler is not None:
            cancelled = await cancel_handler(task_id)
        return {"task_id": task_id, "cancelled": cancelled}

    @router.get("/card", response_model=AgentCard)
    async def get_card() -> AgentCard:
        if card is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No AgentCard configured for this endpoint",
            )
        return card

    return router
