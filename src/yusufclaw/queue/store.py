"""Transactional durable jobs; Redis is never authoritative."""

from __future__ import annotations

import builtins
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import (
    JSON,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

State = Literal["queued", "running", "succeeded", "failed", "cancelled", "waiting_approval"]
TERMINAL = {"succeeded", "failed", "cancelled"}
metadata = MetaData()
jobs = Table(
    "queue_jobs",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("user_id", String(255), nullable=False),
    Column("idempotency_key", String(255)),
    Column("state", String(32), nullable=False, index=True),
    Column("priority", Integer, nullable=False),
    Column("run_at", Float, nullable=False, index=True),
    Column("data", JSON, nullable=False),
    UniqueConstraint("user_id", "idempotency_key"),
)
limits_table = Table(
    "queue_limits",
    metadata,
    Column("scope", String(16), primary_key=True),
    Column("identity", String(255), primary_key=True),
    Column("data", JSON, nullable=False),
)
gate = Table("queue_gate", metadata, Column("id", Integer, primary_key=True))
events = Table(
    "queue_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("job_id", String(64), nullable=False, index=True),
    Column("data", JSON, nullable=False),
)


class Job(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    user_id: str = "local"
    role: str = "default"
    state: State = "queued"
    priority: int = 0
    run_at: float = Field(default_factory=time.time)
    created_at: float = Field(default_factory=time.time)
    idempotency_key: str | None = None
    parent_id: str | None = None
    concurrency_key: str | None = None
    attempts: int = 0
    max_attempts: int = Field(default=3, ge=1)
    backoff: float = Field(default=1, ge=0)
    lease_token: str | None = None
    lease_until: float = 0
    worker_id: str | None = None
    dead_letter: bool = False
    error: str | None = None
    result: dict[str, Any] | None = None
    budget: float = Field(default=0, ge=0)
    charged: float = 0
    timeout: float = Field(default=120, gt=0)


class Limit(BaseModel):
    concurrency: int = Field(default=3, ge=1)
    budget: float | None = Field(default=None, ge=0)


class LeaseLost(RuntimeError):
    pass


class JobQueue:
    def __init__(
        self,
        url: str = "sqlite+aiosqlite:///queue.sqlite",
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        self.engine = create_async_engine(url)
        self.clock = clock

    async def initialize(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            # Both supported dialects implement ON CONFLICT.
            from sqlalchemy import text

            await conn.execute(
                text("INSERT INTO queue_gate (id) VALUES (1) ON CONFLICT DO NOTHING")
            )

    async def close(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        async with self.engine.begin() as conn:
            # Serialize admission and accounting across processes, including SQLite.
            await conn.execute(update(gate).where(gate.c.id == 1).values(id=1))
            yield conn

    async def _save(self, conn: AsyncConnection, job: Job, event: str | None = None) -> None:
        await conn.execute(
            update(jobs)
            .where(jobs.c.id == job.id)
            .values(
                state=job.state, priority=job.priority, run_at=job.run_at, data=job.model_dump()
            )
        )
        if event:
            await conn.execute(
                insert(events).values(
                    job_id=job.id,
                    data={
                        "type": "CUSTOM",
                        "name": "job." + event,
                        "value": {"job_id": job.id, "state": job.state, "attempt": job.attempts},
                        "timestamp": self.clock(),
                    },
                )
            )

    async def _get(self, conn: AsyncConnection, job_id: str) -> Job:
        row = (await conn.execute(select(jobs.c.data).where(jobs.c.id == job_id))).scalar_one()
        return Job.model_validate(row)

    async def enqueue(
        self, kind: str, payload: dict[str, Any] | None = None, **options: Any
    ) -> Job:
        job = Job(kind=kind, payload=payload or {}, **options)
        if job.state != "queued" or job.attempts or job.charged:
            raise ValueError("New jobs must be unstarted")
        async with self.transaction() as conn:
            if job.idempotency_key is not None:
                old = (
                    await conn.execute(
                        select(jobs.c.data).where(
                            jobs.c.user_id == job.user_id,
                            jobs.c.idempotency_key == job.idempotency_key,
                        )
                    )
                ).scalar_one_or_none()
                if old is not None:
                    return Job.model_validate(old)
            if job.parent_id:
                parent = await self._get(conn, job.parent_id)
                if parent.user_id != job.user_id or parent.state in TERMINAL:
                    raise ValueError("Parent must be active and owned by the same user")
            await conn.execute(
                insert(jobs).values(
                    id=job.id,
                    user_id=job.user_id,
                    idempotency_key=job.idempotency_key,
                    state=job.state,
                    priority=job.priority,
                    run_at=job.run_at,
                    data=job.model_dump(),
                )
            )
            await self._save(conn, job, "queued")
        return job

    async def set_limit(self, scope: Literal["user", "role"], identity: str, limit: Limit) -> None:
        if scope not in {"user", "role"}:
            raise ValueError("Invalid limit scope")
        async with self.transaction() as conn:
            condition = (limits_table.c.scope == scope) & (limits_table.c.identity == identity)
            exists = (await conn.execute(select(limits_table.c.data).where(condition))).first()
            if exists:
                await conn.execute(
                    update(limits_table).where(condition).values(data=limit.model_dump())
                )
            else:
                await conn.execute(
                    insert(limits_table).values(
                        scope=scope, identity=identity, data=limit.model_dump()
                    )
                )

    async def get(self, job_id: str) -> Job:
        async with self.engine.connect() as conn:
            return await self._get(conn, job_id)

    async def list(self, state: State | None = None) -> builtins.list[Job]:
        async with self.engine.connect() as conn:
            query = select(jobs.c.data).order_by(jobs.c.run_at, jobs.c.id)
            if state:
                query = query.where(jobs.c.state == state)
            return [Job.model_validate(row) for row in (await conn.execute(query)).scalars()]

    def _retry(self, job: Job) -> None:
        job.lease_token = None
        job.lease_until = 0
        job.worker_id = None
        if job.attempts >= job.max_attempts:
            job.state = "failed"
            job.dead_letter = True
        else:
            job.state = "queued"
            job.run_at = self.clock() + job.backoff * 2 ** min(job.attempts - 1, 30)

    async def claim(
        self,
        worker_id: str,
        *,
        lease: float = 30,
        kinds: builtins.list[str] | None = None,
        job_id: str | None = None,
    ) -> Job | None:
        if lease <= 0:
            raise ValueError("Lease must be positive")
        async with self.transaction() as conn:
            policies = (await conn.execute(select(limits_table))).all()
            users = {
                r.identity: Limit.model_validate(r.data) for r in policies if r.scope == "user"
            }
            roles = {
                r.identity: Limit.model_validate(r.data) for r in policies if r.scope == "role"
            }
            all_jobs = [
                Job.model_validate(v) for v in (await conn.execute(select(jobs.c.data))).scalars()
            ]
            for j in all_jobs:
                if j.state == "running" and j.lease_until <= self.clock():
                    j.error = "lease_expired"
                    self._retry(j)
                    await self._save(conn, j, "reclaimed")
            query = (
                select(jobs.c.data)
                .where(jobs.c.state == "queued", jobs.c.run_at <= self.clock())
                .order_by(jobs.c.priority.desc(), jobs.c.run_at, jobs.c.id)
            )
            if job_id:
                query = query.where(jobs.c.id == job_id)
            query = query.with_for_update(skip_locked=True)
            for raw in (await conn.execute(query)).scalars():
                job = Job.model_validate(raw)
                if kinds is not None and job.kind not in kinds:
                    continue
                allowed = not any(
                    j.state == "running"
                    and job.concurrency_key is not None
                    and j.user_id == job.user_id
                    and j.concurrency_key == job.concurrency_key
                    for j in all_jobs
                )
                for attr, limits in [("user_id", users or {}), ("role", roles or {})]:
                    identity = getattr(job, attr)
                    limit = limits.get(identity, Limit())
                    group = [j for j in all_jobs if getattr(j, attr) == identity]
                    running = sum(j.state == "running" for j in group)
                    spent = sum(j.charged for j in group)
                    if running >= limit.concurrency or (
                        limit.budget is not None and spent + job.budget > limit.budget
                    ):
                        allowed = False
                if not allowed:
                    continue
                job.state = "running"
                job.attempts += 1
                job.charged += job.budget
                job.worker_id = worker_id
                job.lease_token = uuid.uuid4().hex
                job.lease_until = self.clock() + lease
                await self._save(conn, job, "running")
                return job
        return None

    def _fence(self, job: Job, token: str) -> None:
        if job.state != "running" or job.lease_token != token or job.lease_until <= self.clock():
            raise LeaseLost(job.id)

    async def heartbeat(self, job_id: str, token: str, lease: float = 30) -> None:
        if lease <= 0:
            raise ValueError("Lease must be positive")
        async with self.transaction() as conn:
            job = await self._get(conn, job_id)
            self._fence(job, token)
            job.lease_until = self.clock() + lease
            await self._save(conn, job)

    async def finish(
        self,
        job_id: str,
        token: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        waiting_approval: bool = False,
    ) -> Job:
        async with self.transaction() as conn:
            job = await self._get(conn, job_id)
            self._fence(job, token)
            job.result = result
            job.error = error
            if error:
                self._retry(job)
            else:
                job.state = "waiting_approval" if waiting_approval else "succeeded"
                job.lease_token = None
                job.lease_until = 0
                job.worker_id = None
            await self._save(conn, job, job.state)
            return job

    async def resume(self, job_id: str) -> Job:
        """Trusted caller must validate the action-bound approval before resuming."""
        async with self.transaction() as conn:
            job = await self._get(conn, job_id)
            if job.state != "waiting_approval":
                raise ValueError("Job is not waiting for approval")
            job.state = "queued"
            job.run_at = self.clock()
            await self._save(conn, job, "resumed")
            return job

    async def cancel(self, job_id: str) -> None:
        async with self.transaction() as conn:
            await self._get(conn, job_id)
            pending = [job_id]
            all_jobs = [
                Job.model_validate(v) for v in (await conn.execute(select(jobs.c.data))).scalars()
            ]
            while pending:
                current = pending.pop()
                pending.extend(j.id for j in all_jobs if j.parent_id == current)
                job = next(j for j in all_jobs if j.id == current)
                if job.state not in TERMINAL:
                    job.state = "cancelled"
                    job.lease_token = None
                    job.lease_until = 0
                    await self._save(conn, job, "cancelled")

    async def progress(self, job_id: str, token: str, percent: int) -> None:
        if not 0 <= percent <= 100:
            raise ValueError("Progress must be between 0 and 100")
        async with self.transaction() as conn:
            job = await self._get(conn, job_id)
            self._fence(job, token)
            await conn.execute(
                insert(events).values(
                    job_id=job_id,
                    data={
                        "type": "CUSTOM",
                        "name": "job.progress",
                        "value": {"job_id": job_id, "percent": percent},
                        "timestamp": self.clock(),
                    },
                )
            )

    async def events(self, job_id: str, after: int = 0) -> builtins.list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = await conn.execute(
                select(events)
                .where(events.c.job_id == job_id, events.c.id > after)
                .order_by(events.c.id)
            )
            return [dict(row.data, id=row.id) for row in rows]
