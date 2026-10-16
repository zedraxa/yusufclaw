"""Durable state-machine, worker and integration acceptance tests."""

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateSchema, DropSchema

from yusufclaw.queue import JobQueue, LeaseLost, Limit, WaitingApproval, Worker
from yusufclaw.queue.store import jobs


@pytest.fixture
async def queue(tmp_path):
    url = os.environ.get("QUEUE_TEST_POSTGRES_URL")
    admin = JobQueue(url) if url else None
    schema = "queue_test_" + uuid.uuid4().hex
    if admin:
        async with admin.engine.begin() as conn:
            await conn.execute(CreateSchema(schema))
        url = (
            str(admin.engine.url.render_as_string(hide_password=False))
            + "?options=-csearch_path%3D"
            + schema
        )
    q = JobQueue(url or f"sqlite+aiosqlite:///{tmp_path}/queue.sqlite")
    now = [100.0]
    q.clock = lambda: now[0]
    await q.initialize()
    yield q, now
    await q.close()
    if admin:
        async with admin.engine.begin() as conn:
            await conn.execute(DropSchema(schema, cascade=True))
        await admin.close()


async def test_success_progress_events_and_fencing(queue):
    q, _ = queue
    job = await q.enqueue("test", run_at=100)
    running = await q.claim("w")
    assert running.id == job.id and running.attempts == 1
    await q.progress(job.id, running.lease_token, 50)
    await q.heartbeat(job.id, running.lease_token)
    finished = await q.finish(job.id, running.lease_token, result={"ok": True})
    assert finished.state == "succeeded"
    events = await q.events(job.id)
    assert [e["name"] for e in events] == [
        "job.queued",
        "job.running",
        "job.progress",
        "job.succeeded",
    ]
    assert len(await q.events(job.id, events[1]["id"])) == 2
    for operation in [
        q.heartbeat(job.id, running.lease_token),
        q.finish(job.id, running.lease_token),
        q.progress(job.id, running.lease_token, 3),
    ]:
        with pytest.raises(LeaseLost):
            await operation
    await q.cancel(job.id)
    assert (await q.get(job.id)).state == "succeeded"


async def test_retry_backoff_dead_letter(queue):
    q, now = queue
    job = await q.enqueue("test", run_at=100, backoff=2)
    for attempt, delay in [(1, 2), (2, 4), (3, 0)]:
        running = await q.claim("w")
        assert running.attempts == attempt
        result = await q.finish(job.id, running.lease_token, error="transient")
        if delay:
            assert result.state == "queued" and result.run_at == now[0] + delay
            assert await q.claim("early") is None
            now[0] += delay
        else:
            assert result.state == "failed" and result.dead_letter
    assert await q.claim("w") is None


async def test_lease_reclaim_heartbeat_and_stale_owner(queue):
    q, now = queue
    job = await q.enqueue("test", run_at=100, backoff=0, max_attempts=2)
    first = await q.claim("dead", lease=5)
    now[0] += 4
    await q.heartbeat(job.id, first.lease_token, 5)
    now[0] += 2
    assert await q.claim("new") is None
    now[0] += 3
    with pytest.raises(LeaseLost):
        await q.finish(job.id, first.lease_token)
    second = await q.claim("new", lease=1)
    assert second.id == first.id and second.lease_token != first.lease_token
    with pytest.raises(LeaseLost):
        await q.heartbeat(job.id, first.lease_token)
    now[0] += 1
    assert await q.claim("last") is None
    assert (await q.get(job.id)).dead_letter


@pytest.mark.parametrize("state", ["queued", "running", "waiting_approval"])
async def test_cancel_nonterminal_and_descendants(queue, state):
    q, _ = queue
    parent = await q.enqueue("parent", run_at=100)
    child = await q.enqueue("child", parent_id=parent.id, run_at=100)
    if state != "queued":
        running = await q.claim("w", job_id=parent.id)
        if state == "waiting_approval":
            await q.finish(parent.id, running.lease_token, waiting_approval=True)
    await q.cancel(parent.id)
    await q.cancel(parent.id)
    assert (await q.get(parent.id)).state == "cancelled"
    assert (await q.get(child.id)).state == "cancelled"
    assert await q.claim("w") is None
    with pytest.raises(ValueError):
        await q.enqueue("child", parent_id=parent.id)


async def test_wait_resume_and_reject_invalid_transitions(queue):
    q, _ = queue
    job = await q.enqueue("test", run_at=100)
    with pytest.raises(ValueError):
        await q.resume(job.id)
    with pytest.raises(LeaseLost):
        await q.finish(job.id, "wrong")
    running = await q.claim("w")
    await q.finish(job.id, running.lease_token, waiting_approval=True)
    assert await q.claim("w") is None
    await q.resume(job.id)
    resumed = await q.claim("w")
    await q.finish(job.id, resumed.lease_token)
    with pytest.raises(ValueError):
        await q.resume(job.id)


async def test_idempotency_concurrent_producers_and_restart(queue):
    q, _ = queue
    other = JobQueue(q.engine.url.render_as_string(hide_password=False))
    try:
        results = await asyncio.gather(
            *[
                (q if i % 2 else other).enqueue("test", idempotency_key="once", run_at=100)
                for i in range(12)
            ]
        )
        assert len({j.id for j in results}) == 1
        assert len(await other.list()) == 1
        distinct = await other.enqueue("test", idempotency_key="once", user_id="other")
        assert distinct.id != results[0].id
    finally:
        await other.close()


async def test_priority_schedule_and_concurrent_claims(queue):
    q, now = queue
    low = await q.enqueue("test", run_at=100)
    high = await q.enqueue("test", run_at=100, priority=10)
    future = await q.enqueue("test", run_at=200, priority=100)
    assert (await q.claim("w")).id == high.id
    claimed = await asyncio.gather(q.claim("a"), q.claim("b"))
    assert [j.id for j in claimed if j] == [low.id]
    now[0] = 200
    # Expired jobs back off; the scheduled high priority job wins.
    assert (await q.claim("c")).id == future.id


@pytest.mark.parametrize("scope,identity", [("user", "local"), ("role", "default")])
async def test_durable_concurrency_and_budget_admission(queue, scope, identity):
    q, _ = queue
    await q.set_limit(scope, identity, Limit(concurrency=1, budget=5))
    first = await q.enqueue("test", run_at=100, budget=3)
    second = await q.enqueue("test", run_at=100, budget=3)
    running = await q.claim("a", job_id=first.id)
    assert await q.claim("b") is None
    await q.finish(first.id, running.lease_token)
    assert await q.claim("b") is None
    await q.set_limit(scope, identity, Limit(concurrency=1, budget=6))
    assert (await q.claim("b")).id == second.id


async def test_parent_scope_and_concurrency_key(queue):
    q, _ = queue
    parent = await q.enqueue("parent", run_at=100, concurrency_key="session")
    await q.enqueue("child", run_at=100, concurrency_key="session", parent_id=parent.id)
    with pytest.raises(ValueError):
        await q.enqueue("child", parent_id=parent.id, user_id="intruder")
    await q.claim("a")
    assert await q.claim("b") is None


async def test_worker_success_failure_wait_timeout(queue):
    q, _ = queue

    async def handler(job):
        if job.kind == "fail":
            raise RuntimeError("private error")
        if job.kind == "wait":
            raise WaitingApproval()
        if job.kind == "timeout":
            await asyncio.sleep(10)
        return {"ok": True}

    worker = Worker(q, {kind: handler for kind in ["ok", "fail", "wait", "timeout"]})
    for kind, state in [
        ("ok", "succeeded"),
        ("fail", "failed"),
        ("wait", "waiting_approval"),
        ("timeout", "failed"),
    ]:
        job = await q.enqueue(kind, run_at=100, max_attempts=1, timeout=0.02)
        assert await worker.run_once(job.id)
        result = await q.get(job.id)
        assert result.state == state
        assert "private" not in (result.error or "")


async def test_worker_observes_cancellation_and_heartbeats(tmp_path):
    q = JobQueue(f"sqlite+aiosqlite:///{tmp_path}/worker.sqlite")
    await q.initialize()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(job):
        started.set()
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()
        return {}

    try:
        job = await q.enqueue("slow")
        task = asyncio.create_task(Worker(q, {"slow": handler}, lease=0.15).run_once())
        await started.wait()
        initial = (await q.get(job.id)).lease_until
        await asyncio.sleep(0.2)
        assert (await q.get(job.id)).lease_until > initial
        await q.cancel(job.id)
        await asyncio.wait_for(task, 1)
        assert cancelled.is_set()
        assert (await q.get(job.id)).state == "cancelled"
    finally:
        await q.close()


async def test_routine_enqueues_and_worker_delivers(queue):
    from yusufclaw.routines import Routine, RoutineScheduler, RoutineTarget

    q, now = queue
    now[0] = 60
    calls = []

    async def transport(message, signal):
        calls.append(message.request_id)

    routine = Routine("daily", "* * * * *", "hello", RoutineTarget("cli", "local", "x"))
    scheduler = RoutineScheduler([routine], object(), transport, job_queue=q)
    await scheduler.tick(datetime.fromtimestamp(60, UTC))
    await scheduler.tick(datetime.fromtimestamp(60, UTC))
    assert calls == []
    assert len(await q.list()) == 1
    worker = Worker(q, {"routine": scheduler.handle_job})
    assert await worker.run_once()
    assert calls == ["routine:daily:1"]


async def test_subagent_uses_queue_by_default(queue):
    from yusufclaw.models.fake import FakeProvider
    from yusufclaw.orchestration.models import SubagentSpec
    from yusufclaw.orchestration.subagent import SubagentRunner

    q, now = queue
    import time

    now[0] = time.time() + 1
    parent = await q.enqueue("parent")
    runner = SubagentRunner(FakeProvider(default_text="done"), job_queue=q, parent_job_id=parent.id)
    result = await runner.run(SubagentSpec(name="research", instruction="summarize"))
    assert result.success
    children = [j for j in await q.list() if j.parent_id == parent.id]
    assert len(children) == 1 and children[0].state == "succeeded"


def test_postgres_claim_lock_clause():
    sql = str(select(jobs).with_for_update(skip_locked=True).compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql


async def test_shutdown_leaves_reclaimable_lease(queue):
    q, now = queue
    started = asyncio.Event()

    async def handler(job):
        started.set()
        await asyncio.sleep(10)
        return {}

    job = await q.enqueue("shutdown", run_at=100, backoff=0)
    task = asyncio.create_task(Worker(q, {"shutdown": handler}, lease=3).run_once())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await q.get(job.id)).state == "running"
    now[0] += 3
    assert (await q.claim("replacement")).attempts == 2


async def test_failed_parent_reclaim_cancels_children(queue):
    q, now = queue
    parent = await q.enqueue("parent", run_at=100, max_attempts=1)
    child = await q.enqueue("child", run_at=100, parent_id=parent.id)
    await q.claim("dead", job_id=parent.id, lease=1)
    now[0] += 1
    assert await q.claim("replacement") is None
    assert (await q.get(parent.id)).state == "failed"
    assert (await q.get(child.id)).state == "cancelled"


async def test_skip_locked_live_postgres(queue):
    q, _ = queue
    first = await q.enqueue("test", run_at=100, priority=1)
    second = await q.enqueue("test", run_at=100)
    if q.engine.dialect.name == "postgresql":
        async with q.engine.begin() as conn:
            await conn.execute(select(jobs).where(jobs.c.id == first.id).with_for_update())
            claimed = await asyncio.wait_for(q.claim("worker"), 2)
            assert claimed.id == second.id
        assert (await q.claim("next")).id == first.id
    else:
        assert (await q.claim("worker")).id == first.id
        assert (await q.claim("next")).id == second.id


async def test_orchestrator_creates_parent_child_jobs():
    from yusufclaw.models.fake import FakeProvider
    from yusufclaw.orchestration.models import ExecutionPlan, PlanStep
    from yusufclaw.orchestration.orchestrator import AdaptiveOrchestrator
    from yusufclaw.queue import default_queue

    orchestrator = AdaptiveOrchestrator(provider=FakeProvider(default_text="done"))
    orchestrator.budget_manager.start_turn()
    await orchestrator._execute_plan(
        "research",
        ExecutionPlan(
            goal="research",
            steps=[PlanStep(step_id=1, description="research", tool_or_subagent="subagent")],
        ),
    )
    q = default_queue()
    try:
        all_jobs = await q.list()
        root = next(j for j in all_jobs if j.kind == "orchestration")
        assert root.state == "succeeded"
        assert any(j.parent_id == root.id and j.state == "succeeded" for j in all_jobs)
    finally:
        await q.close()


async def test_worker_span_restores_enqueue_trace(queue, monkeypatch):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)
    q, _ = queue

    async def handler(job):
        return {}

    with provider.get_tracer("test").start_as_current_span("producer") as parent:
        job = await q.enqueue("traced", run_at=100)
        parent_id = parent.get_span_context().span_id
    await Worker(q, {"traced": handler}).run_once(job.id)
    span = next(s for s in exporter.get_finished_spans() if s.name == "yusufclaw.queue.execute")
    assert span.parent.span_id == parent_id
    assert span.attributes["job.id"] == job.id
    provider.shutdown()
