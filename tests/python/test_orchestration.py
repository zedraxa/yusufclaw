"""Comprehensive offline test suite for YusufClaw adaptive orchestration.

Tests:
1. TaskAnalyzer: deterministic heuristics, tool detection, risk levels, model assist, fallback.
2. BudgetManager: token counting, pricing calculation, steps, time, limits, graceful stop.
3. Verifier: rule-based acceptance criteria, model judge, retry recommendation.
4. SubagentRunner: depth 1 clamping, least-privilege tool intersection, budget tracking.
5. PlanAndExecuteWorkflow: LangGraph planning, step execution, retry, synthesis.
6. AdaptiveOrchestrator: full turn orchestration, strategy routing, verification retry.
7. Agent integration: flag-gated orchestration vs default loop.
8. OpenTelemetry tracing verification.
"""

from __future__ import annotations

import json

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from yusufclaw.agent import Agent
from yusufclaw.models.fake import FakeProvider
from yusufclaw.models.types import ModelResponse, ToolCall, ToolDefinition
from yusufclaw.orchestration import (
    AcceptanceCriteria,
    AdaptiveOrchestrator,
    BudgetExceededError,
    BudgetLimits,
    BudgetManager,
    OrchestrationConfig,
    PlanAndExecuteWorkflow,
    SubagentRunner,
    SubagentSpec,
    TaskAnalyzer,
    TokenPricing,
    Verifier,
)
from yusufclaw.security import Grants
from yusufclaw.telemetry import reset_telemetry, setup_telemetry


@pytest.fixture(autouse=True)
def otel_in_memory() -> InMemorySpanExporter:
    """Setup in-memory OTel exporter for verifying orchestration spans."""
    reset_telemetry()
    exporter = InMemorySpanExporter()
    setup_telemetry(service_name="test-orchestration", span_exporter=exporter, force_reinit=True)
    return exporter


# ---------------------------------------------------------------------------
# 1. TaskAnalyzer Tests
# ---------------------------------------------------------------------------


def test_task_analyzer_heuristic_simple() -> None:
    analyzer = TaskAnalyzer()
    analysis = analyzer.analyze_heuristic("What is the capital of France?")
    assert analysis.complexity == "simple"
    assert analysis.risk == "low"
    assert analysis.strategy == "direct"
    assert not analysis.plan_required
    assert not analysis.delegate_required


def test_task_analyzer_heuristic_tools_and_risk() -> None:
    analyzer = TaskAnalyzer()

    # Tool detection and medium risk
    res_write = analyzer.analyze_heuristic("Please write file data.txt with new user information")
    assert "file_write" in res_write.required_tools
    assert res_write.risk == "medium"

    # Shell and high risk
    res_high = analyzer.analyze_heuristic("run command: sudo kill -9 1234 and delete token")
    assert "shell" in res_high.required_tools
    assert res_high.risk == "high"

    # Critical risk
    res_crit = analyzer.analyze_heuristic("Execute bash: rm -rf /var/data and drop table users")
    assert res_crit.risk == "critical"


def test_task_analyzer_heuristic_multi_step_and_delegation() -> None:
    analyzer = TaskAnalyzer()

    # Sequential conjunction
    multi_step_prompt = (
        "First fetch https://api.example.com/items then write file report.txt and then run tests"
    )
    analysis = analyzer.analyze_heuristic(multi_step_prompt)
    assert analysis.complexity == "complex"
    assert len(analysis.required_tools) >= 2
    assert analysis.strategy in ("plan_and_execute", "delegate")

    # Explicit delegation
    delegate_prompt = "Delegate this research task to a subagent in parallel"
    analysis_del = analyzer.analyze_heuristic(delegate_prompt)
    assert analysis_del.delegate_required
    assert analysis_del.strategy == "delegate"


@pytest.mark.asyncio
async def test_task_analyzer_model_assisted_success() -> None:
    fake_json = json.dumps(
        {
            "complexity": "complex",
            "required_tools": ["file_read", "shell"],
            "risk": "medium",
            "plan_required": True,
            "delegate_required": False,
            "strategy": "plan_and_execute",
            "reasoning": "Multi-step build process",
            "subtasks": ["Read config", "Run build script"],
            "estimated_steps": 2,
        }
    )
    provider = FakeProvider(
        canned_responses=[
            ModelResponse(text=f"```json\n{fake_json}\n```", tool_calls=[])
        ]
    )
    analyzer = TaskAnalyzer(provider=provider)
    analysis = await analyzer.analyze("Build the project", model_assisted=True)

    assert analysis.complexity == "complex"
    assert analysis.strategy == "plan_and_execute"
    assert analysis.subtasks == ["Read config", "Run build script"]
    assert analysis.estimated_steps == 2


@pytest.mark.asyncio
async def test_task_analyzer_model_assisted_fallback_on_invalid_json() -> None:
    provider = FakeProvider(canned_responses=[ModelResponse(text="Not a json response")])
    analyzer = TaskAnalyzer(provider=provider)
    # Should fall back to heuristic without raising an exception
    analysis = await analyzer.analyze("What is 2 + 2?", model_assisted=True)
    assert analysis.complexity == "simple"
    assert "Model assist fallback" in analysis.reasoning


# ---------------------------------------------------------------------------
# 2. BudgetManager Tests
# ---------------------------------------------------------------------------


def test_budget_manager_token_and_cost_accounting() -> None:
    pricing = TokenPricing(prompt_cost_per_1m=4.0, completion_cost_per_1m=16.0)
    limits = BudgetLimits(max_tokens_per_turn=1000, max_cost_per_turn=0.10)
    bm = BudgetManager(limits=limits, pricing=pricing)

    bm.start_turn()
    cost1 = bm.record_tokens(prompt_tokens=500_000, completion_tokens=100_000)
    # Expected: (0.5 * 4.0) + (0.1 * 16.0) = 2.0 + 1.6 = $3.60
    assert pytest.approx(cost1, rel=1e-3) == 3.60
    assert bm.turn_usage.total_tokens == 600_000
    assert bm.session_usage.total_tokens == 600_000

    bm.record_step(2)
    assert bm.turn_usage.steps == 2
    assert bm.session_usage.steps == 2

    # End turn
    summary = bm.end_turn()
    assert summary.total_tokens == 600_000


def test_budget_manager_limit_checks_and_graceful_stop() -> None:
    limits = BudgetLimits(
        max_tokens_per_turn=500,
        max_steps_per_turn=3,
        max_cost_per_turn=1.0,
    )
    bm = BudgetManager(limits=limits)
    bm.start_turn()

    # Within limits initially
    status_init = bm.check_limits()
    assert not status_init.is_exceeded

    # Exceed steps
    bm.record_step(3)
    status_steps = bm.check_limits()
    assert status_steps.is_exceeded
    assert "Turn step limit exceeded" in (status_steps.exceeded_reason or "")

    # Exceed tokens
    bm.record_tokens(300, 300)  # total 600 > 500
    status_toks = bm.check_limits()
    assert status_toks.is_exceeded

    # Graceful stop message formatting
    msg = bm.get_graceful_stop_message(status_toks.exceeded_reason)
    assert "Operation halted gracefully" in msg
    assert "Tokens=600" in msg

    # raise_if_exceeded
    with pytest.raises(BudgetExceededError):
        bm.raise_if_exceeded()


def test_budget_manager_session_persistence_across_turns() -> None:
    limits = BudgetLimits(max_tokens_per_session=1000, max_tokens_per_turn=600)
    bm = BudgetManager(limits=limits)

    # Turn 1
    bm.start_turn()
    bm.record_tokens(200, 200)
    bm.record_step(1)
    bm.end_turn()
    assert bm.turn_usage.total_tokens == 400
    assert bm.session_usage.total_tokens == 400

    # Turn 2
    bm.start_turn()
    assert bm.turn_usage.total_tokens == 0
    assert bm.session_usage.total_tokens == 400
    bm.record_tokens(300, 200)  # Session becomes 900
    assert bm.session_usage.total_tokens == 900
    bm.end_turn()

    # Turn 3 exceeds session ceiling
    bm.start_turn()
    bm.record_tokens(100, 100)  # Session becomes 1100 > 1000
    status = bm.check_limits()
    assert status.is_exceeded
    assert "Session token limit exceeded" in (status.exceeded_reason or "")


# ---------------------------------------------------------------------------
# 3. Verifier Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verifier_rule_based_acceptance() -> None:
    verifier = Verifier()

    criteria = AcceptanceCriteria(
        min_length=10,
        max_length=200,
        required_keywords=["Paris", "France"],
        forbidden_keywords=["London", "error"],
        required_pattern=r"capital\s+is",
        must_not_contain_patterns=[r"\bdeprecated\b"],
    )

    # Valid candidate
    good_text = "The capital is Paris, the largest city of France."
    res_good = await verifier.verify(good_text, criteria)
    assert res_good.passed
    assert res_good.score == 1.0
    assert not res_good.retry_recommended

    # Failing min length
    res_short = await verifier.verify("Paris", criteria)
    assert not res_short.passed
    assert any("below minimum" in r for r in res_short.reasons)

    # Failing forbidden keyword
    bad_kw = "The capital is Paris, France. London error!"
    res_bad = await verifier.verify(bad_kw, criteria)
    assert not res_bad.passed
    assert any("Forbidden keyword 'London'" in r for r in res_bad.reasons)


@pytest.mark.asyncio
async def test_verifier_retry_recommendation() -> None:
    verifier = Verifier()
    criteria = AcceptanceCriteria(required_keywords=["Summary", "Findings"])

    output = "Here are the details."
    res = await verifier.verify(output, criteria, retries_attempted=0, max_retries=1)
    assert not res.passed
    assert res.retry_recommended
    assert res.retry_prompt is not None
    assert "Summary" in res.retry_prompt

    # Max retries reached
    res_no_retry = await verifier.verify(output, criteria, retries_attempted=1, max_retries=1)
    assert not res_no_retry.passed
    assert not res_no_retry.retry_recommended


@pytest.mark.asyncio
async def test_verifier_model_judge() -> None:
    judge_json = json.dumps({"passed": True, "score": 0.95, "reason": "Accurate response"})
    provider = FakeProvider(canned_responses=[ModelResponse(text=judge_json)])
    verifier = Verifier(provider=provider)

    criteria = AcceptanceCriteria(min_length=5, model_judge_required=True)
    res = await verifier.verify("Accurate output answering question.", criteria)
    assert res.passed
    assert res.score == 0.95


# ---------------------------------------------------------------------------
# 4. SubagentRunner Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subagent_runner_boundary_and_budget() -> None:
    provider = FakeProvider(
        canned_responses=[
            ModelResponse(text="Subagent worker completed task successfully.", tool_calls=[])
        ]
    )
    bm = BudgetManager()
    bm.start_turn()

    tool_echo = ToolDefinition(
        name="echo",
        description="Echo input",
        input_schema={"type": "object"},
    )
    runner = SubagentRunner(
        provider=provider,
        parent_tools=[tool_echo],
        budget_manager=bm,
    )

    spec = SubagentSpec(
        name="researcher",
        instruction="Analyze the report",
        permitted_tools=["echo"],
        max_depth=3,  # Should be clamped to 1
        max_steps=2,
    )

    result = await runner.run(spec)
    assert result.success
    assert "Subagent worker completed task" in result.output
    assert spec.max_depth == 1
    # Check parent budget was deducted
    assert bm.turn_usage.steps >= 1
    assert bm.turn_usage.total_tokens > 0


@pytest.mark.asyncio
async def test_subagent_runner_least_privilege_tool_filtering() -> None:
    provider = FakeProvider(
        canned_responses=[
            # Model attempts calling an unauthorized tool
            ModelResponse(
                text="",
                tool_calls=[ToolCall(id="tc-1", name="dangerous_tool", input={})],
            ),
            # Final response after rejection
            ModelResponse(text="I cannot access that tool.", tool_calls=[]),
        ]
    )
    safe_tool = ToolDefinition(name="safe_tool", description="Safe", input_schema={})
    runner = SubagentRunner(
        provider=provider,
        parent_tools=[safe_tool],
        parent_grants=Grants(allowed_tools={"safe_tool"}),
    )

    spec = SubagentSpec(
        name="guarded_worker",
        instruction="Try dangerous tool",
        permitted_tools=["dangerous_tool"],  # Not in parent tools
        max_steps=3,
    )

    result = await runner.run(spec)
    assert result.success
    assert "I cannot access that tool" in result.output


# ---------------------------------------------------------------------------
# 5. PlanAndExecuteWorkflow Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_and_execute_workflow() -> None:
    plan_json = json.dumps(
        {
            "steps": [
                {"step_id": 1, "description": "Step 1: Inspect environment"},
                {"step_id": 2, "description": "Step 2: Generate output"},
            ]
        }
    )
    provider = FakeProvider(
        canned_responses=[
            # 1. Planner model response
            ModelResponse(text=f"```json\n{plan_json}\n```"),
            # 2. Step 1 execution
            ModelResponse(text="Environment inspection completed: Linux x86_64"),
            # 3. Step 2 execution
            ModelResponse(text="Output generated: Status OK"),
            # 4. Synthesizer model response
            ModelResponse(text="Final synthesized report: Everything is configured and status is OK."),
        ]
    )
    bm = BudgetManager()
    bm.start_turn()

    subagent_runner = SubagentRunner(provider=provider, budget_manager=bm)
    planner = PlanAndExecuteWorkflow(
        provider=provider,
        subagent_runner=subagent_runner,
        budget_manager=bm,
    )

    res = await planner.execute("Deploy service")
    assert "Final synthesized report" in res
    assert bm.turn_usage.steps >= 3


# ---------------------------------------------------------------------------
# 6. AdaptiveOrchestrator Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adaptive_orchestrator_direct_path_and_verification_retry() -> None:
    # First response fails verification (missing required keyword "Verified"),
    # retry response includes it and passes.
    provider = FakeProvider(
        canned_responses=[
            # Retry model response
            ModelResponse(text="Here is the revised response. Verified successfully."),
        ]
    )
    config = OrchestrationConfig(enabled=True, auto_retry_verification=True)
    orchestrator = AdaptiveOrchestrator(provider=provider, config=config)

    turn_count = 0

    async def direct_turn() -> str:
        nonlocal turn_count
        turn_count += 1
        return "Initial candidate response."

    criteria = AcceptanceCriteria(required_keywords=["Verified"])

    output, analysis = await orchestrator.orchestrate_turn(
        prompt="Tell me a simple fact",
        session_id="session-1",
        direct_turn_fn=direct_turn,
        criteria=criteria,
    )

    assert analysis.strategy == "direct"
    assert "Verified successfully" in output


@pytest.mark.asyncio
async def test_adaptive_orchestrator_budget_exhaustion_graceful_stop() -> None:
    provider = FakeProvider()
    limits = BudgetLimits(max_tokens_per_session=50, max_tokens_per_turn=10)
    config = OrchestrationConfig(enabled=True, budget_limits=limits)
    orchestrator = AdaptiveOrchestrator(provider=provider, config=config)

    # Exhaust session budget before turn
    orchestrator.budget_manager.record_tokens(30, 30)

    async def direct_turn() -> str:
        return "Should not execute"

    output, _ = await orchestrator.orchestrate_turn(
        prompt="Execute task",
        session_id="session-2",
        direct_turn_fn=direct_turn,
    )

    assert "Operation halted gracefully" in output
    assert "token limit" in output.lower()

    # Also test turn budget exceeded during execution
    fresh_limits = BudgetLimits(max_tokens_per_session=1000, max_tokens_per_turn=20)
    fresh_orchestrator = AdaptiveOrchestrator(
        provider=provider,
        config=OrchestrationConfig(enabled=True, budget_limits=fresh_limits),
    )

    async def exhausting_turn() -> str:
        fresh_orchestrator.budget_manager.record_tokens(20, 20)
        return "Executed but exceeded turn tokens"

    output2, _ = await fresh_orchestrator.orchestrate_turn(
        prompt="Execute heavy task",
        session_id="session-3",
        direct_turn_fn=exhausting_turn,
    )
    assert "Operation halted gracefully" in output2
    assert "token limit" in output2.lower()


# ---------------------------------------------------------------------------
# 7. Agent Integration Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_orchestration_flag_default_disabled() -> None:
    provider = FakeProvider(canned_responses=[ModelResponse(text="Standard response without orchestration")])
    agent = Agent(provider=provider)
    assert not agent.enable_orchestration
    assert agent.orchestrator is None

    reply = await agent.run("Hello world")
    assert reply == "Standard response without orchestration"


@pytest.mark.asyncio
async def test_agent_orchestration_flag_enabled() -> None:
    provider = FakeProvider(canned_responses=[ModelResponse(text="Orchestrated turn response")])
    agent = Agent(provider=provider, enable_orchestration=True)
    assert agent.enable_orchestration
    assert agent.orchestrator is not None

    reply = await agent.run("Hello orchestrated agent")
    assert reply == "Orchestrated turn response"


@pytest.mark.asyncio
async def test_agent_handle_orchestrated() -> None:
    provider = FakeProvider(canned_responses=[ModelResponse(text="Handled orchestrated message")])
    agent = Agent(provider=provider, enable_orchestration=True)

    reply = await agent.handle("sess-orch", "req-1", "Explain LangGraph")
    assert reply == "Handled orchestrated message"


# ---------------------------------------------------------------------------
# 8. OpenTelemetry Tracing Verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestration_otel_spans(otel_in_memory: InMemorySpanExporter) -> None:
    provider = FakeProvider(canned_responses=[ModelResponse(text="Response with telemetry tracing")])
    agent = Agent(provider=provider, enable_orchestration=True)

    await agent.run("Task triggering telemetry spans")

    spans = otel_in_memory.get_finished_spans()
    span_names = [s.name for s in spans]

    assert "yusufclaw.orchestration.turn" in span_names
    assert "yusufclaw.orchestration.task_analysis" in span_names
    assert "yusufclaw.orchestration.budget_check" in span_names
    assert "yusufclaw.orchestration.verification" in span_names
