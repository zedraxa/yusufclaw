"""Routine scheduler with single-flight tick and queue-backed concurrency control."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from yusufclaw.queue import Job, JobQueue, Worker, default_queue, queue_enabled
from yusufclaw.routines.cron import parse_cron
from yusufclaw.routines.models import Routine, RoutineMessage, RoutineTarget
from yusufclaw.routines.runner import (
    ChannelRunner,
    ChannelRunnerOptions,
    RoutineRunner,
    RoutineTransport,
    SingleFlightQueue,
    check_aborted,
    is_aborted,
)


async def get_memory_state(memory: Any, key: str) -> str | None:
    """Read state from memory store supporting sync and async interfaces."""
    fn = getattr(memory, "get_state", None) or getattr(memory, "getState", None)
    if fn is None:
        return None
    res = fn(key)
    if inspect.iscoroutine(res):
        value = await res
        return str(value) if value is not None else None
    return str(res) if res is not None else None


async def set_memory_state(memory: Any, key: str, value: str) -> None:
    """Store state in memory store supporting sync and async interfaces."""
    fn = getattr(memory, "set_state", None) or getattr(memory, "setState", None)
    if fn is not None:
        res = fn(key, value)
        if inspect.iscoroutine(res):
            await res


class RoutineScheduler:
    """One process per config/database.

    Missed minutes are skipped; failed deliveries retry within their minute.
    Queue-backed single-flight ensures the same routine ID cannot run concurrently.
    """

    def __init__(
        self,
        routines: Sequence[Routine],
        memory: Any,
        transport: RoutineTransport | RoutineRunner | Any,
        on_error: Callable[[], None] | None = None,
        queue: SingleFlightQueue | None = None,
        job_queue: JobQueue | None = None,
        durable: bool | None = None,
    ) -> None:
        self.durable = queue_enabled() if durable is None else durable
        self.job_queue = job_queue
        self.routines = list(routines)
        self.memory = memory
        self.transport = transport
        self.on_error = on_error or (lambda: None)
        self.queue = queue or SingleFlightQueue()
        self.entries = [(routine, parse_cron(routine.cron)) for routine in self.routines]
        self._running_tick: asyncio.Task[None] | None = None

    def tick(
        self,
        now: datetime | None = None,
        signal: Any = None,
    ) -> asyncio.Task[None]:
        """Trigger due routines for the current minute.

        If a tick is already in-flight, returns the running task (single-flight tick).
        """
        if self._running_tick is not None and not self._running_tick.done():
            return self._running_tick

        current_now = now or datetime.now(UTC)
        task = asyncio.create_task(self._execute(current_now, signal))
        self._running_tick = task

        def _on_done(t: asyncio.Task[None]) -> None:
            if self._running_tick is t:
                self._running_tick = None

        task.add_done_callback(_on_done)
        return task

    async def _execute(self, now: datetime, signal: Any = None) -> None:
        # Minute timestamp matching TypeScript: Math.floor(now.getTime() / 60_000)
        minute = int(now.timestamp() // 60)

        for routine, matches in self.entries:
            check_aborted(signal)
            key = routine.dedup_key

            if not matches(now):
                continue

            last_run = await get_memory_state(self.memory, key)
            if last_run is not None:
                try:
                    if float(last_run) >= minute:
                        continue
                except ValueError:
                    pass

            request_id = f"routine:{routine.id}:{minute}"
            message = RoutineMessage(
                channel=routine.target.channel,
                user_id=routine.target.user_id,
                conversation_id=routine.target.conversation_id,
                request_id=request_id,
                text=routine.prompt,
            )

            if self.durable:
                store = self.job_queue or default_queue()
                try:
                    await store.initialize()
                    await store.enqueue(
                        "routine",
                        {
                            "routine_id": routine.id,
                            "cron": routine.cron,
                            "prompt": routine.prompt,
                            "target": routine.target.to_dict(),
                            "request_id": request_id,
                            "minute": minute,
                            "key": key,
                        },
                        user_id=routine.target.user_id,
                        role="scheduler",
                        idempotency_key=f"{key}:{minute}",
                        run_at=minute * 60,
                        concurrency_key=key,
                    )
                finally:
                    if self.job_queue is None:
                        await store.close()
                continue

            # Queue-backed single-flight execution
            async def _run_job(
                r: Routine = routine,
                m: RoutineMessage = message,
                req: str = request_id,
                min_val: int = minute,
                k: str = key,
            ) -> None:
                await self._run_single_routine(r, m, req, min_val, k, signal)

            await self.queue.run_single_flight(routine.id, _run_job)

    async def handle_job(self, job: Job) -> dict[str, Any]:
        """Register with Worker; exceptions trigger durable retries."""
        data = job.payload
        target = data["target"]
        routine = Routine(
            id=data["routine_id"],
            cron=data["cron"],
            prompt=data["prompt"],
            target=RoutineTarget(
                channel=target["channel"],
                user_id=target["userId"],
                conversation_id=target["conversationId"],
            ),
        )
        message = RoutineMessage(
            channel=routine.target.channel,
            user_id=job.user_id,
            conversation_id=routine.target.conversation_id,
            request_id=data["request_id"],
            text=routine.prompt,
        )
        await self._run_single_routine(
            routine, message, data["request_id"], data["minute"], data["key"], propagate=True
        )
        return {"delivered": True}

    async def _run_single_routine(
        self,
        routine: Routine,
        message: RoutineMessage,
        request_id: str,
        minute: int,
        key: str,
        signal: Any = None,
        propagate: bool = False,
    ) -> None:
        try:
            check_aborted(signal)

            # Check if transport has dispatch + deliver (TS RoutineTransport)
            dispatch_fn = getattr(self.transport, "dispatch", None)
            deliver_fn = getattr(self.transport, "deliver", None)

            if dispatch_fn is not None and deliver_fn is not None:
                res_dispatch = dispatch_fn(message, signal)
                if inspect.iscoroutine(res_dispatch):
                    reply = await res_dispatch
                else:
                    reply = res_dispatch

                check_aborted(signal)

                res_deliver = deliver_fn(routine.target, str(reply), request_id, signal)
                if inspect.iscoroutine(res_deliver):
                    await res_deliver

            # Check if transport is a RoutineRunner
            elif hasattr(self.transport, "run_routine"):
                res_run = self.transport.run_routine(routine, request_id, signal)
                if inspect.iscoroutine(res_run):
                    await res_run

            # Check if callable directly
            elif callable(self.transport):
                res_call = self.transport(message, signal)
                if inspect.iscoroutine(res_call):
                    await res_call
            else:
                raise RuntimeError("Incompatible transport provided to RoutineScheduler")

            await set_memory_state(self.memory, key, str(minute))

        except Exception:
            check_aborted(signal)
            self.on_error()
            if propagate:
                raise

    async def run(self, signal: Any = None) -> None:
        """Run the continuous schedule loop until cancelled or signal aborted."""
        store = self.job_queue or default_queue() if self.durable else None
        worker_task: asyncio.Task[None] | None = None
        if store is not None:
            await store.initialize()
            worker_task = asyncio.create_task(Worker(store, {"routine": self.handle_job}).run())
        try:
            while not is_aborted(signal):
                if worker_task is not None and worker_task.done():
                    await worker_task
                await self.tick(datetime.now(UTC), signal)
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass
        finally:
            if worker_task is not None:
                worker_task.cancel()
                await asyncio.gather(worker_task, return_exceptions=True)
            if store is not None and self.job_queue is None:
                await store.close()


def create_routine_scheduler(
    routines: Sequence[Routine],
    memory: Any,
    bus: Any,
    options: ChannelRunnerOptions | dict[str, Any],
) -> RoutineScheduler:
    """Factory creating a RoutineScheduler wired with ChannelRunner."""
    if isinstance(options, dict):
        opts = ChannelRunnerOptions(
            cli_user_id=options.get("cliUserId") or options.get("cli_user_id", "local"),
            telegram_allowed=set(
                options.get("telegramAllowed") or options.get("telegram_allowed", set())
            ),
            telegram=options.get("telegram"),
            web=options.get("web"),
            web_user_id=options.get("webUserId") or options.get("web_user_id", "local"),
            output=options.get("output"),
        )
    else:
        opts = options

    channel_runner = ChannelRunner(bus, opts)
    # Validate each routine target identity upon initialization
    for routine in routines:
        channel_runner.authorize(routine.target)

    return RoutineScheduler(
        routines=routines,
        memory=memory,
        transport=channel_runner,
        on_error=lambda: None,
    )


# CamelCase alias matching TypeScript gateway export
createRoutineScheduler = create_routine_scheduler

__all__ = [
    "RoutineScheduler",
    "createRoutineScheduler",
    "create_routine_scheduler",
    "get_memory_state",
    "set_memory_state",
]
