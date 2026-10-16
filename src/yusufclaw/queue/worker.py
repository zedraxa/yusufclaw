"""Async workers with fenced heartbeats and bounded shutdown."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

import redis.asyncio as redis
from opentelemetry import trace

from yusufclaw.queue.store import Job, JobQueue, LeaseLost

Handler = Callable[[Job], Awaitable[dict[str, Any]]]


def queue_enabled() -> bool:
    return os.environ.get("YUSUFCLAW_QUEUE_ENABLED", "0").lower() in {"1", "true", "yes"}


def default_queue() -> JobQueue:
    return JobQueue(os.environ.get("YUSUFCLAW_QUEUE_URL", "sqlite+aiosqlite:///queue.sqlite"))


class WaitingApproval(Exception):
    """Handler persisted its action-bound approval; release the worker slot."""


class RedisWakeup:
    """Lossy, optional wakeup hints. Polling always remains enabled."""

    def __init__(self, url: str) -> None:
        self.client: redis.Redis[str] = redis.from_url(
            url, decode_responses=True, socket_timeout=1, socket_connect_timeout=1
        )

    async def notify(self) -> None:
        with suppress(Exception):
            await self.client.lpush("yusufclaw:queue:wakeup", "1")
            await self.client.ltrim("yusufclaw:queue:wakeup", 0, 99)

    async def wait(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self.client.blpop("yusufclaw:queue:wakeup", timeout=1), timeout)
        except Exception:
            await asyncio.sleep(min(timeout, 0.1))

    async def close(self) -> None:
        await self.client.close()


class Worker:
    def __init__(
        self,
        queue: JobQueue,
        handlers: dict[str, Handler],
        *,
        lease: float = 30,
        poll: float = 0.5,
        wakeup: RedisWakeup | None = None,
    ) -> None:
        if lease <= 0 or poll <= 0:
            raise ValueError("Worker intervals must be positive")
        self.queue = queue
        self.handlers = handlers
        self.lease = lease
        self.poll = poll
        self.wakeup = wakeup
        self.id = uuid.uuid4().hex

    async def run_once(self, job_id: str | None = None) -> bool:
        job = await self.queue.claim(
            self.id, lease=self.lease, kinds=list(self.handlers), job_id=job_id
        )
        if job is None:
            return False
        assert job.lease_token is not None
        token = job.lease_token

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(self.lease / 3)
                await self.queue.heartbeat(job.id, token, self.lease)

        with trace.get_tracer("yusufclaw.queue").start_as_current_span(
            "yusufclaw.queue.execute"
        ) as span:
            span.set_attributes(
                {
                    "job.id": job.id,
                    "job.kind": job.kind,
                    "job.attempt": job.attempts,
                    "job.parent_id": job.parent_id or "",
                }
            )
            task = asyncio.ensure_future(self.handlers[job.kind](job))
            beat = asyncio.create_task(heartbeat())
            try:
                done, _ = await asyncio.wait(
                    {task, beat}, timeout=job.timeout, return_when=asyncio.FIRST_COMPLETED
                )
                if beat in done:
                    await beat
                if task not in done:
                    raise TimeoutError("Job timed out")
                result = await task
                await self.queue.finish(job.id, token, result=result)
            except WaitingApproval:
                await self.queue.finish(job.id, token, waiting_approval=True)
            except LeaseLost:
                pass
            except asyncio.CancelledError:
                # Leave the lease for another worker to reclaim after shutdown.
                raise
            except Exception as exc:
                span.set_attribute("job.error_type", type(exc).__name__)
                with suppress(LeaseLost):
                    await self.queue.finish(job.id, token, error=type(exc).__name__)
            finally:
                task.cancel()
                beat.cancel()
                await asyncio.gather(task, beat, return_exceptions=True)
        return True

    async def run(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        while not stop.is_set():
            if not await self.run_once():
                if self.wakeup:
                    await self.wakeup.wait(self.poll)
                else:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), self.poll)
