"""Offline tests for YusufClaw Agent-to-Agent (A2A 1.0) local teammate protocol.

Tests cover:
1. AgentCard, TaskRequest, TaskResult, and ContextArtifact models.
2. Segregated data channels and memory isolation.
3. TeammateStatusHook (UX-12 events: spawned, step, finished).
4. InProcessTransport (execution, timeout, cancellation).
5. HttpJsonTransport with FastAPI ASGI loopback (offline).
6. TeammateRegistry (depth-1 limit, concurrency ceiling, budget clamping, cancellation propagation).
7. SubagentA2AAdapter and SubagentRunner event hook integration.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from yusufclaw.a2a import (
    ActiveTeammateTask,
    AgentCard,
    ConcurrencyLimitExceededError,
    ContextArtifact,
    DataChannelSegregation,
    DataChannelViolationError,
    DepthLimitExceededError,
    HttpJsonTransport,
    InProcessTransport,
    IsolatedChildMemory,
    MemoryAccessDeniedError,
    SubagentA2AAdapter,
    TaskRequest,
    TaskResult,
    TeammateBudget,
    TeammateEvent,
    TeammateRegistry,
    TeammateStatusHook,
    create_a2a_router,
)
from yusufclaw.models.fake import FakeProvider
from yusufclaw.orchestration.budget import BudgetLimits, BudgetManager
from yusufclaw.orchestration.models import TokenPricing
from yusufclaw.orchestration.subagent import SubagentRunner

# ============================================================================
# 1. Models & Envelope Tests
# ============================================================================


def test_agent_card_creation_and_defaults() -> None:
    card = AgentCard(
        id="teammate_researcher_1",
        role="researcher",
        capabilities=["web_fetch", "file_read"],
        budget=TeammateBudget(max_tokens=4000, timeout_seconds=60),
        name="Literature Explorer",
        description="Specialist in academic and document analysis",
    )
    assert card.id == "teammate_researcher_1"
    assert card.role == "researcher"
    assert card.capabilities == ["web_fetch", "file_read"]
    assert card.budget.max_tokens == 4000
    assert card.display_name == "Literature Explorer"
    assert card.version == "1.0.0"

    # Alias / camelCase support
    card_from_dict = AgentCard.model_validate({"agentId": "worker_2", "role": "coder"})
    assert card_from_dict.id == "worker_2"
    assert card_from_dict.display_name == "worker_2"


def test_task_request_and_result_envelopes() -> None:
    req = TaskRequest(
        target_agent_id="worker_1",
        instruction="Analyze the security findings",
        context_artifacts=[
            ContextArtifact(name="report.txt", content="Found 2 issues", mime_type="text/plain")
        ],
        budget=TeammateBudget(max_tokens=1500, timeout_seconds=15),
    )
    assert req.target_agent_id == "worker_1"
    assert req.depth == 1
    assert len(req.context_artifacts) == 1
    assert req.context_artifacts[0].untrusted is True

    res = TaskResult(
        task_id=req.task_id,
        agent_id=req.target_agent_id,
        status="success",
        output="Analysis completed successfully.",
        tokens_used=120,
        steps_used=2,
    )
    assert res.task_id == req.task_id
    assert res.status == "success"
    assert res.tokens_used == 120


# ============================================================================
# 2. Segregated Data Channels & Memory Isolation Tests
# ============================================================================


def test_data_channel_segregation_prepares_context() -> None:
    req = TaskRequest(
        target_agent_id="specialist_1",
        instruction="Summarize research findings",
        context_artifacts=[
            ContextArtifact(name="notes.md", content="User prefers python"),
            ContextArtifact(name="paper.pdf", content="Abstract text..."),
        ],
    )
    ctx = DataChannelSegregation.prepare_context(req, role_name="researcher")
    assert ctx.instruction == req.instruction
    assert len(ctx.artifacts) == 2
    assert "UNTRUSTED CONTRUSTED" not in ctx.system_prompt
    assert "[SEGREGATED DATA CHANNEL: UNTRUSTED CONTEXT ARTIFACTS]" in ctx.system_prompt
    assert "Artifact #1: 'notes.md'" in ctx.system_prompt
    assert "Artifact #2: 'paper.pdf'" in ctx.system_prompt
    assert ctx.isolated_session_id.startswith("a2a:child:")


def test_data_channel_segregation_rejects_depth_greater_than_one() -> None:
    req = TaskRequest(
        target_agent_id="specialist_1",
        instruction="Do something",
        depth=2,
    )
    with pytest.raises(DataChannelViolationError, match="exceeds depth-1 limit"):
        DataChannelSegregation.prepare_context(req)


def test_data_channel_segregation_rejects_forbidden_memory_keys() -> None:
    req = TaskRequest(
        target_agent_id="specialist_1",
        instruction="Do something",
        metadata={"parent_memory": "leak"},
    )
    with pytest.raises(DataChannelViolationError, match="Forbidden parent memory reference"):
        DataChannelSegregation.prepare_context(req)


def test_verify_no_parent_memory_access() -> None:
    # Denies exact parent scope access
    with pytest.raises(MemoryAccessDeniedError, match="cannot read parent memory scope"):
        DataChannelSegregation.verify_no_parent_memory_access(
            requested_scope="web:user_123:conv_456",
            parent_scope="web:user_123:conv_456",
        )

    # Denies wildcard read
    with pytest.raises(MemoryAccessDeniedError, match="wildcard memory read"):
        DataChannelSegregation.verify_no_parent_memory_access(
            requested_scope="*",
            parent_scope="web:user_123:conv_456",
        )

    # Denies profile read
    with pytest.raises(MemoryAccessDeniedError, match="wildcard memory read"):
        DataChannelSegregation.verify_no_parent_memory_access(
            requested_scope="profile",
            parent_scope="web:user_123:conv_456",
        )


def test_isolated_child_memory() -> None:
    child_mem = IsolatedChildMemory(task_id="task_abc123")
    assert child_mem.scope == "a2a:child:task_abc123"

    child_mem.remember("sub_fact", "value_1")
    assert child_mem.recall("sub_fact") == "value_1"

    child_mem.record_turn("user", "Hello")
    child_mem.record_turn("assistant", "Hi")
    assert len(child_mem.get_turns()) == 2

    # Denies cross-scope access
    with pytest.raises(MemoryAccessDeniedError, match="denied access to foreign scope"):
        child_mem.check_access("web:parent:conv_1")


# ============================================================================
# 3. TeammateStatusHook Tests (UX-12)
# ============================================================================


@pytest.mark.asyncio
async def test_teammate_status_hook_lifecycle() -> None:
    hook = TeammateStatusHook()
    received_events: list[TeammateEvent] = []

    def sync_listener(event: TeammateEvent) -> None:
        received_events.append(event)

    hook.add_listener(sync_listener)

    # Emit spawned
    await hook.emit_spawned(
        subagent_id="researcher_1",
        parent_run_id="run_root",
        role="researcher",
        task="Gather evidence",
        tools=["web_fetch"],
        max_cost=0.05,
    )

    # Emit step
    await hook.emit_step(
        subagent_id="researcher_1",
        step=1,
        summary="Fetched arXiv document",
        tokens=350,
    )

    # Emit finished
    await hook.emit_finished(
        subagent_id="researcher_1",
        status="success",
        artifact_ref="art_999",
        cost_usd=0.012,
    )

    assert len(received_events) == 3
    assert received_events[0].event_type == "teammate.spawned"
    assert received_events[0].subagent_id == "researcher_1"
    assert received_events[0].tools == ["web_fetch"]

    assert received_events[1].event_type == "teammate.step"
    assert received_events[1].step == 1
    assert received_events[1].tokens == 350

    assert received_events[2].event_type == "teammate.finished"
    assert received_events[2].status == "success"
    assert received_events[2].cost_usd == 0.012

    assert len(hook.history) == 3
    hook.clear_history()
    assert len(hook.history) == 0


# ============================================================================
# 4. InProcessTransport Tests
# ============================================================================


@pytest.mark.asyncio
async def test_in_process_transport_success() -> None:
    async def sample_handler(req: TaskRequest) -> TaskResult:
        return TaskResult(
            task_id=req.task_id,
            agent_id=req.target_agent_id,
            status="success",
            output=f"Done: {req.instruction}",
            tokens_used=150,
        )

    transport = InProcessTransport()
    transport.register_handler("agent_a", sample_handler)

    req = TaskRequest(
        target_agent_id="agent_a",
        instruction="Run calculations",
    )
    result = await transport.send_task(req)
    assert result.status == "success"
    assert "Done: Run calculations" in result.output
    assert result.duration_ms > 0.0


@pytest.mark.asyncio
async def test_in_process_transport_timeout_enforced() -> None:
    async def slow_handler(req: TaskRequest) -> TaskResult:
        await asyncio.sleep(0.5)
        return TaskResult(
            task_id=req.task_id,
            agent_id=req.target_agent_id,
            status="success",
        )

    transport = InProcessTransport(default_handler=slow_handler)
    req = TaskRequest(
        target_agent_id="agent_slow",
        instruction="Sleep a lot",
        budget=TeammateBudget(timeout_seconds=0.05),
    )

    result = await transport.send_task(req)
    assert result.status == "timeout"
    assert "timed out" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_in_process_transport_cancellation() -> None:
    started_evt = asyncio.Event()

    async def cancellable_handler(req: TaskRequest) -> TaskResult:
        started_evt.set()
        await asyncio.sleep(2.0)
        return TaskResult(
            task_id=req.task_id,
            agent_id=req.target_agent_id,
            status="success",
        )

    transport = InProcessTransport(default_handler=cancellable_handler)
    req = TaskRequest(target_agent_id="worker_c", instruction="Long task")

    task = asyncio.create_task(transport.send_task(req))
    await started_evt.wait()

    # Cancel via transport
    cancelled = await transport.cancel_task(req.task_id)
    assert cancelled is True

    res = await task
    assert res.status == "cancelled"


# ============================================================================
# 5. HttpJsonTransport Tests (Offline ASGI Loopback)
# ============================================================================


@pytest.mark.asyncio
async def test_http_json_transport_offline_asgi() -> None:
    async def endpoint_handler(req: TaskRequest) -> TaskResult:
        return TaskResult(
            task_id=req.task_id,
            agent_id=req.target_agent_id,
            status="success",
            output=f"Echo: {req.instruction}",
        )

    agent_card = AgentCard(id="http_worker_1", role="coder", capabilities=["python"])
    router = create_a2a_router(endpoint_handler, card=agent_card)

    app = FastAPI()
    app.include_router(router)

    # Use httpx.AsyncClient with ASGITransport (100% offline, zero network)
    asgi_transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=asgi_transport, base_url="http://testserver") as client:
        transport = HttpJsonTransport(base_url="http://testserver", client=client)

        req = TaskRequest(
            target_agent_id="http_worker_1",
            instruction="Format this code",
        )
        res = await transport.send_task(req)
        assert res.status == "success"
        assert res.output == "Echo: Format this code"

        # Cancel endpoint
        cancelled = await transport.cancel_task(req.task_id)
        assert cancelled is True


# ============================================================================
# 6. TeammateRegistry Tests
# ============================================================================


@pytest.mark.asyncio
async def test_teammate_registry_registration_and_dispatch() -> None:
    registry = TeammateRegistry()

    card = AgentCard(
        id="teammate_ops",
        role="ops",
        capabilities=["docker", "bash"],
        budget=TeammateBudget(max_tokens=2000, timeout_seconds=10),
    )

    async def ops_handler(req: TaskRequest) -> TaskResult:
        return TaskResult(
            task_id=req.task_id,
            agent_id=req.target_agent_id,
            status="success",
            output="Container started",
        )

    in_proc = InProcessTransport(default_handler=ops_handler)
    registry.register(card, transport=in_proc)

    # Lookup
    assert registry.get("teammate_ops") is not None
    assert registry.get("ops") is not None  # Lookup by role fallback
    assert len(registry.list_teammates()) == 1

    # Dispatch
    req = TaskRequest(
        target_agent_id="teammate_ops",
        instruction="Start development service",
    )
    result = await registry.dispatch_task(req)
    assert result.status == "success"
    assert result.output == "Container started"


@pytest.mark.asyncio
async def test_teammate_registry_enforces_depth_one_limit() -> None:
    registry = TeammateRegistry()
    card = AgentCard(id="worker_1", role="worker")
    registry.register(card)

    # Direct request with depth=2 is rejected
    req_deep = TaskRequest(
        target_agent_id="worker_1",
        instruction="Deep task",
        depth=2,
    )
    with pytest.raises(DepthLimitExceededError, match="exceeds maximum allowed depth of 1"):
        await registry.dispatch_task(req_deep)


@pytest.mark.asyncio
async def test_teammate_registry_rejects_grandchildren() -> None:
    registry = TeammateRegistry()
    card = AgentCard(id="child_worker", role="worker")
    registry.register(card)

    # Simulate an active child task already running in registry
    active_child = ActiveTeammateTask(
        task_id="task_child_1",
        parent_task_id="task_root_parent",
        caller_id="root_coordinator",
        agent_id="child_worker",
        depth=1,
        started_at=100.0,
        budget=TeammateBudget(),
        transport=registry.default_transport,
    )
    registry._active_tasks["task_child_1"] = active_child

    # Now child tries to spawn a grandchild
    req_grandchild = TaskRequest(
        target_agent_id="child_worker",
        parent_task_id="task_child_1",
        caller_id="child_worker",
        instruction="Spawn another worker",
        depth=1,
    )

    with pytest.raises(DepthLimitExceededError, match="grandchildren are strictly prohibited"):
        await registry.dispatch_task(req_grandchild)


@pytest.mark.asyncio
async def test_teammate_registry_concurrency_ceiling() -> None:
    registry = TeammateRegistry(max_concurrent_workers=2)
    card = AgentCard(id="heavy_worker", role="worker")
    registry.register(card)

    # Add 2 active children for parent 'task_parent_100'
    registry._parent_to_children["task_parent_100"] = {"task_child_a", "task_child_b"}

    # 3rd request from same parent should be rejected by concurrency ceiling
    req_3 = TaskRequest(
        target_agent_id="heavy_worker",
        parent_task_id="task_parent_100",
        instruction="Third worker task",
    )
    with pytest.raises(ConcurrencyLimitExceededError, match="Concurrency ceiling reached"):
        await registry.dispatch_task(req_3)


@pytest.mark.asyncio
async def test_teammate_registry_budget_clamping() -> None:
    registry = TeammateRegistry()
    card = AgentCard(
        id="frugal_worker",
        role="worker",
        budget=TeammateBudget(max_tokens=1000, timeout_seconds=10),
    )

    captured_budget: TeammateBudget | None = None

    async def inspect_handler(req: TaskRequest) -> TaskResult:
        nonlocal captured_budget
        captured_budget = req.budget
        return TaskResult(task_id=req.task_id, agent_id=req.target_agent_id, status="success")

    registry.register(card, transport=InProcessTransport(default_handler=inspect_handler))

    # Request with higher budget is clamped to card limits
    req = TaskRequest(
        target_agent_id="frugal_worker",
        instruction="Task",
        budget=TeammateBudget(max_tokens=50000, timeout_seconds=120),
    )
    await registry.dispatch_task(req)

    assert captured_budget is not None
    assert captured_budget.max_tokens == 1000
    assert captured_budget.timeout_seconds == 10


@pytest.mark.asyncio
async def test_teammate_registry_cancellation_propagation() -> None:
    registry = TeammateRegistry()
    in_proc = InProcessTransport()
    card = AgentCard(id="long_worker", role="worker")
    registry.register(card, transport=in_proc)

    started_evts = [asyncio.Event(), asyncio.Event()]

    async def long_handler(req: TaskRequest) -> TaskResult:
        if req.task_id == "child_task_1":
            started_evts[0].set()
        else:
            started_evts[1].set()
        await asyncio.sleep(2.0)
        return TaskResult(task_id=req.task_id, agent_id=req.target_agent_id, status="success")

    in_proc.register_handler("long_worker", long_handler)

    # Launch two child tasks under same parent run
    req1 = TaskRequest(
        task_id="child_task_1",
        parent_task_id="parent_run_999",
        target_agent_id="long_worker",
        instruction="Work 1",
    )
    req2 = TaskRequest(
        task_id="child_task_2",
        parent_task_id="parent_run_999",
        target_agent_id="long_worker",
        instruction="Work 2",
    )

    task1 = asyncio.create_task(registry.dispatch_task(req1))
    task2 = asyncio.create_task(registry.dispatch_task(req2))

    await started_evts[0].wait()
    await started_evts[1].wait()

    # Now cancel the parent!
    cancelled_count = await registry.cancel_parent("parent_run_999")
    assert cancelled_count == 2

    res1 = await task1
    res2 = await task2
    assert res1.status == "cancelled"
    assert res2.status == "cancelled"


# ============================================================================
# 7. SubagentA2AAdapter and SubagentRunner Hook Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_subagent_runner_publishes_teammate_events() -> None:
    hook = TeammateStatusHook()
    provider = FakeProvider(default_text="Subagent research complete")
    bm = BudgetManager(limits=BudgetLimits(max_tokens=1000), pricing=TokenPricing())

    runner = SubagentRunner(
        provider=provider,
        budget_manager=bm,
        teammate_hook=hook,
        user_id="test_user",
        parent_job_id="parent_job_123",
        durable=False,
    )

    # Wrap runner with SubagentA2AAdapter
    adapter = SubagentA2AAdapter(runner)

    req = TaskRequest(
        task_id="task_sub_1",
        target_agent_id="researcher_agent",
        instruction="Investigate quantum algorithms",
        context_artifacts=[
            ContextArtifact(name="input.txt", content="Quantum papers list"),
        ],
        budget=TeammateBudget(max_tokens=500, timeout_seconds=10),
    )

    result = await adapter.send_task(req)
    assert result.status == "success"
    assert "Subagent research complete" in result.output

    # Check published UX-12 events
    events = hook.history
    assert len(events) >= 3

    spawned = [e for e in events if e.event_type == "teammate.spawned"]
    steps = [e for e in events if e.event_type == "teammate.step"]
    finished = [e for e in events if e.event_type == "teammate.finished"]

    assert len(spawned) == 1
    assert spawned[0].subagent_id == "researcher_agent"
    assert spawned[0].parent_run_id == "parent_job_123"

    assert len(steps) >= 1
    assert steps[0].subagent_id == "researcher_agent"
    assert steps[0].tokens is not None

    assert len(finished) == 1
    assert finished[0].subagent_id == "researcher_agent"
    assert finished[0].status == "success"
