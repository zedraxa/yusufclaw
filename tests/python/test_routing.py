"""Comprehensive offline unit and integration tests for adaptive model routing.

Tests:
1. ModelRole and RoutingConfig: defaults, custom maps, environment variable construction.
2. ModelRouter resolution:
   - Explicit role routing (planner/synthesis/judge -> strong, classify/search/transform -> cheap).
   - Single-provider fallback when only 1 FakeProvider is configured (offline tests guarantee).
   - Missing provider fallback to default/fake.
   - Dynamic provider registration and route re-binding.
   - ModelRouter as ModelProvider (complete delegating to default).
3. TaskAnalysis-driven routing:
   - Prefer cheap path for simple complexity + low risk.
   - High/critical risk or complex tasks route to strong provider.
4. Orchestration component wiring:
   - TaskAnalyzer model-assist uses cheap/classify provider.
   - Verifier model judge uses strong/judge provider.
   - PlanAndExecuteWorkflow planner & synthesizer use strong provider.
   - AdaptiveOrchestrator full turn with multi-provider routing and cheap direct path.
   - Agent integration with routed provider execution.
5. OpenTelemetry tracing:
   - Verifies yusufclaw.routing.role and yusufclaw.routing.provider span attributes.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from yusufclaw.agent import Agent
from yusufclaw.config import Settings
from yusufclaw.models import (
    FakeProvider,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelRouter,
    RoutingConfig,
    get_model_router,
)
from yusufclaw.orchestration import (
    AcceptanceCriteria,
    AdaptiveOrchestrator,
    BudgetManager,
    OrchestrationConfig,
    PlanAndExecuteWorkflow,
    SubagentRunner,
    SubagentSpec,
    TaskAnalysis,
    TaskAnalyzer,
    Verifier,
)
from yusufclaw.telemetry import reset_telemetry, setup_telemetry


@pytest.fixture(autouse=True)
def otel_in_memory() -> InMemorySpanExporter:
    """Setup in-memory OTel exporter for verifying model routing spans."""
    reset_telemetry()
    exporter = InMemorySpanExporter()
    setup_telemetry(service_name="test-routing", span_exporter=exporter, force_reinit=True)
    return exporter


# ---------------------------------------------------------------------------
# 1. ModelRole & RoutingConfig Tests
# ---------------------------------------------------------------------------


def test_model_roles_and_default_routes() -> None:
    config = RoutingConfig()
    assert not config.enabled
    assert not config.prefer_cheap_for_simple
    assert config.default_provider == "default"
    assert config.strong_provider == "strong"
    assert config.cheap_provider == "cheap"

    routes = config.role_routes
    assert routes[ModelRole.PLANNER.value] == "strong"
    assert routes[ModelRole.SYNTHESIS.value] == "strong"
    assert routes[ModelRole.JUDGE.value] == "strong"
    assert routes[ModelRole.CLASSIFY.value] == "cheap"
    assert routes[ModelRole.SEARCH.value] == "cheap"
    assert routes[ModelRole.TRANSFORM.value] == "cheap"
    assert routes[ModelRole.HEURISTIC_ASSIST.value] == "cheap"
    assert routes[ModelRole.DEFAULT.value] == "default"


def test_routing_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YC_ROUTING_ENABLED", "true")
    monkeypatch.setenv("YC_ROUTING_DEFAULT_PROVIDER", "custom_def")
    monkeypatch.setenv("YC_ROUTING_STRONG_PROVIDER", "bedrock_claude")
    monkeypatch.setenv("YC_ROUTING_CHEAP_PROVIDER", "ollama_local")
    monkeypatch.setenv("YC_ROUTING_PREFER_CHEAP_FOR_SIMPLE", "1")
    monkeypatch.setenv("YC_ROUTING_ROLE_ROUTES", "planner:bedrock_claude,classify:ollama_local")

    cfg = RoutingConfig.from_env()
    assert cfg.enabled is True
    assert cfg.default_provider == "custom_def"
    assert cfg.strong_provider == "bedrock_claude"
    assert cfg.cheap_provider == "ollama_local"
    assert cfg.prefer_cheap_for_simple is True
    assert cfg.role_routes["planner"] == "bedrock_claude"
    assert cfg.role_routes["classify"] == "ollama_local"


# ---------------------------------------------------------------------------
# 2. ModelRouter Resolution & Fallback Tests
# ---------------------------------------------------------------------------


def test_router_role_selection_strong_and_cheap() -> None:
    strong_provider = FakeProvider(default_text="Response from Strong Provider")
    cheap_provider = FakeProvider(default_text="Response from Cheap Provider")

    router = ModelRouter(
        providers={
            "strong": strong_provider,
            "cheap": cheap_provider,
        }
    )

    # Strong roles
    p_planner, name_planner, _ = router.route(ModelRole.PLANNER)
    assert p_planner is strong_provider
    assert name_planner == "strong"

    p_synth, name_synth, _ = router.route(ModelRole.SYNTHESIS)
    assert p_synth is strong_provider
    assert name_synth == "strong"

    p_judge, name_judge, _ = router.route(ModelRole.JUDGE)
    assert p_judge is strong_provider
    assert name_judge == "strong"

    # Cheap roles
    p_classify, name_classify, _ = router.route(ModelRole.CLASSIFY)
    assert p_classify is cheap_provider
    assert name_classify == "cheap"

    p_search, name_search, _ = router.route(ModelRole.SEARCH)
    assert p_search is cheap_provider
    assert name_search == "cheap"

    p_transform, name_transform, _ = router.route(ModelRole.TRANSFORM)
    assert p_transform is cheap_provider
    assert name_transform == "cheap"


def test_router_single_provider_fallback() -> None:
    """When only a single FakeProvider is registered, any role must fallback safely."""
    sole_fake = FakeProvider(default_text="Sole offline provider")
    router = ModelRouter(default_provider=sole_fake)

    # Any role requests should resolve to the sole provider
    for role in [
        ModelRole.PLANNER,
        ModelRole.SYNTHESIS,
        ModelRole.JUDGE,
        ModelRole.CLASSIFY,
        ModelRole.SEARCH,
        ModelRole.DEFAULT,
    ]:
        p, name, reason = router.route(role)
        assert p is sole_fake
        assert "fallback" in reason.lower() or name == "default"


def test_router_fallback_to_default_when_target_missing() -> None:
    default_fake = FakeProvider(default_text="Default provider")
    cheap_fake = FakeProvider(default_text="Cheap provider")

    # 'strong' is not configured, only 'default' and 'cheap'
    router = ModelRouter(
        providers={
            "default": default_fake,
            "cheap": cheap_fake,
        }
    )

    # Planner mapped to strong -> falls back to default
    p_planner, name, reason = router.route(ModelRole.PLANNER)
    assert p_planner is default_fake
    assert name == "default"
    assert "fallback" in reason.lower()

    # Classify mapped to cheap -> finds cheap
    p_classify, name_c, _ = router.route(ModelRole.CLASSIFY)
    assert p_classify is cheap_fake
    assert name_c == "cheap"


def test_router_key_error_when_empty() -> None:
    router = ModelRouter()
    with pytest.raises(KeyError, match="No model provider found"):
        router.route(ModelRole.PLANNER)


def test_router_dynamic_registration_and_rerouting() -> None:
    p1 = FakeProvider(default_text="P1")
    p2 = FakeProvider(default_text="P2")
    router = ModelRouter(providers={"p1": p1})

    assert router.get_provider(ModelRole.PLANNER) is p1

    router.register_provider("p2", p2)
    router.set_route(ModelRole.PLANNER, "p2")

    assert router.get_provider(ModelRole.PLANNER) is p2


@pytest.mark.asyncio
async def test_router_as_model_provider_complete() -> None:
    fake = FakeProvider(default_text="Routed response")
    router = ModelRouter(default_provider=fake)

    resp = await router.complete(ModelRequest(messages=[]))
    assert resp.text == "Routed response"
    assert len(fake.recorded_requests) == 1


def test_get_model_router_factory() -> None:
    settings = Settings(model_provider="fake")
    router = get_model_router(settings)
    assert isinstance(router, ModelRouter)
    assert "fake" in router.registered_providers
    assert router.get_provider(ModelRole.PLANNER) is not None


# ---------------------------------------------------------------------------
# 3. TaskAnalysis-Driven Routing Tests (Cheap Path)
# ---------------------------------------------------------------------------


def test_router_task_analysis_prefer_cheap_for_simple() -> None:
    strong = FakeProvider(default_text="Strong")
    cheap = FakeProvider(default_text="Cheap")
    default = FakeProvider(default_text="Default")

    cfg = RoutingConfig(prefer_cheap_for_simple=True)
    router = ModelRouter(
        providers={"strong": strong, "cheap": cheap, "default": default},
        config=cfg,
    )

    # Simple + low risk -> routed to cheap
    simple_analysis = TaskAnalysis(complexity="simple", risk="low")
    p_simple, name_simple, reason_simple = router.route(ModelRole.DEFAULT, analysis=simple_analysis)
    assert p_simple is cheap
    assert name_simple == "cheap"
    assert "cheap" in reason_simple.lower()

    # Complex + low risk -> routed to strong
    complex_analysis = TaskAnalysis(complexity="complex", risk="low")
    p_complex, name_complex, _ = router.route(ModelRole.DEFAULT, analysis=complex_analysis)
    assert p_complex is strong
    assert name_complex == "strong"

    # Simple + high risk -> routed to strong
    high_risk_analysis = TaskAnalysis(complexity="simple", risk="high")
    p_high, name_high, _ = router.route(ModelRole.DEFAULT, analysis=high_risk_analysis)
    assert p_high is strong
    assert name_high == "strong"

    # Simple + critical risk -> routed to strong
    crit_analysis = TaskAnalysis(complexity="simple", risk="critical")
    p_crit, name_crit, _ = router.route(ModelRole.DEFAULT, analysis=crit_analysis)
    assert p_crit is strong
    assert name_crit == "strong"


def test_router_task_analysis_disabled_prefer_cheap() -> None:
    strong = FakeProvider(default_text="Strong")
    cheap = FakeProvider(default_text="Cheap")
    default = FakeProvider(default_text="Default")

    cfg = RoutingConfig(prefer_cheap_for_simple=False)
    router = ModelRouter(
        providers={"strong": strong, "cheap": cheap, "default": default},
        config=cfg,
    )

    # When disabled, simple + low risk stays on default
    simple_analysis = TaskAnalysis(complexity="simple", risk="low")
    p, name, _ = router.route(ModelRole.DEFAULT, analysis=simple_analysis)
    assert p is default
    assert name == "default"


# ---------------------------------------------------------------------------
# 4. Orchestration Component Routing Integration Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_analyzer_uses_cheap_provider() -> None:
    strong = FakeProvider(default_text="Strong should not be called")
    cheap_json = json.dumps(
        {
            "complexity": "simple",
            "required_tools": ["file_read"],
            "risk": "low",
            "plan_required": False,
            "delegate_required": False,
            "strategy": "direct",
            "reasoning": "Analyzed by cheap model",
            "subtasks": [],
            "estimated_steps": 1,
        }
    )
    cheap = FakeProvider(canned_responses=[ModelResponse(text=cheap_json)])

    router = ModelRouter(providers={"strong": strong, "cheap": cheap})
    analyzer = TaskAnalyzer(router=router)

    analysis = await analyzer.analyze("Read file test.txt", model_assisted=True)
    assert analysis.complexity == "simple"
    assert "file_read" in analysis.required_tools
    # Verify cheap provider was called, strong was untouched
    assert len(cheap.recorded_requests) == 1
    assert len(strong.recorded_requests) == 0


@pytest.mark.asyncio
async def test_verifier_model_judge_uses_strong_provider() -> None:
    cheap = FakeProvider(default_text="Cheap should not judge")
    judge_json = json.dumps(
        {
            "passed": True,
            "score": 0.98,
            "reason": "Verified by strong judge",
        }
    )
    strong = FakeProvider(canned_responses=[ModelResponse(text=judge_json)])

    router = ModelRouter(providers={"strong": strong, "cheap": cheap})
    verifier = Verifier(router=router)

    criteria = AcceptanceCriteria(model_judge_required=True)
    res = await verifier.verify("Candidate text output", criteria, prompt="User goal")
    assert res.passed is True
    assert res.score == 0.98
    # Verify strong provider was called, cheap was untouched
    assert len(strong.recorded_requests) == 1
    assert len(cheap.recorded_requests) == 0


@pytest.mark.asyncio
async def test_planner_and_synthesizer_use_strong_provider() -> None:
    cheap = FakeProvider(default_text="Step executed cheaply")
    plan_json = json.dumps(
        {
            "steps": [
                {"step_id": 1, "description": "Gather data"},
                {"step_id": 2, "description": "Process data"},
            ]
        }
    )
    synthesis_text = "Final synthesized answer from strong model."
    strong = FakeProvider(
        canned_responses=[
            ModelResponse(text=plan_json),  # Planner call
            ModelResponse(text=synthesis_text),  # Synthesizer call
        ]
    )

    router = ModelRouter(
        providers={
            "strong": strong,
            "cheap": cheap,
            "default": cheap,
        }
    )
    bm = BudgetManager()
    sub_runner = SubagentRunner(provider=cheap, router=router)
    verifier = Verifier(router=router)

    workflow = PlanAndExecuteWorkflow(
        provider=strong,
        subagent_runner=sub_runner,
        budget_manager=bm,
        verifier=verifier,
        router=router,
    )

    final_resp = await workflow.execute("Goal requiring multi-step plan")
    assert "Final synthesized answer" in final_resp

    # Strong was called for planning and synthesis (2 calls)
    assert len(strong.recorded_requests) == 2
    # Cheap (default) was called for the 2 discrete steps
    assert len(cheap.recorded_requests) >= 2


@pytest.mark.asyncio
async def test_adaptive_orchestrator_cheap_path_direct_turn() -> None:
    """Verify that when prefer_cheap_for_simple is enabled, direct turn runs against cheap provider."""
    cheap_executed = False
    strong_executed = False

    cheap_p = FakeProvider(default_text="Cheap response")
    strong_p = FakeProvider(default_text="Strong response")

    router = ModelRouter(
        providers={"strong": strong_p, "cheap": cheap_p},
        config=RoutingConfig(prefer_cheap_for_simple=True),
    )

    orchestrator = AdaptiveOrchestrator(
        router=router,
        config=OrchestrationConfig(enabled=True),
    )

    async def direct_turn(provider: Any = None) -> str:
        nonlocal cheap_executed, strong_executed
        if provider is cheap_p:
            cheap_executed = True
        elif provider is strong_p:
            strong_executed = True
        return "Executed direct turn"

    output, analysis = await orchestrator.orchestrate_turn(
        prompt="What is the weather?",
        session_id="s1",
        direct_turn_fn=direct_turn,
    )

    assert analysis.complexity == "simple"
    assert analysis.risk == "low"
    assert cheap_executed is True
    assert strong_executed is False
    assert output == "Executed direct turn"


@pytest.mark.asyncio
async def test_adaptive_orchestrator_default_fallback_preserves_single_provider() -> None:
    """Verify backwards-compatibility: single FakeProvider runs smoothly without routing config."""
    sole_fake = FakeProvider(default_text="Default response")
    orchestrator = AdaptiveOrchestrator(
        provider=sole_fake,
        config=OrchestrationConfig(enabled=True),
    )

    executed = False

    async def direct_turn() -> str:
        nonlocal executed
        executed = True
        return "Turn finished"

    output, _ = await orchestrator.orchestrate_turn(
        prompt="Hello world",
        session_id="s2",
        direct_turn_fn=direct_turn,
    )

    assert executed is True
    assert output == "Turn finished"


@pytest.mark.asyncio
async def test_agent_integration_with_router() -> None:
    """Verify Agent can take a ModelRouter and execute direct turn with cheap provider."""
    cheap_fake = FakeProvider(default_text="Answer from cheap agent provider.")
    strong_fake = FakeProvider(default_text="Answer from strong agent provider.")

    router = ModelRouter(
        providers={"strong": strong_fake, "cheap": cheap_fake},
        config=RoutingConfig(prefer_cheap_for_simple=True),
    )

    agent = Agent(
        router=router,
        enable_orchestration=True,
        orchestration_config=OrchestrationConfig(enabled=True),
    )

    reply = await agent.run("Simple explanation request", session_id="session-agent")
    assert "Answer from cheap agent provider" in reply
    assert len(cheap_fake.recorded_requests) == 1
    assert len(strong_fake.recorded_requests) == 0


# ---------------------------------------------------------------------------
# 5. OpenTelemetry Span Attribute Verification Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opentelemetry_routing_attributes_emitted(
    otel_in_memory: InMemorySpanExporter,
) -> None:
    cheap = FakeProvider(default_text="Cheap")
    strong = FakeProvider(default_text="Strong")

    router = ModelRouter(
        providers={"strong": strong, "cheap": cheap},
        config=RoutingConfig(prefer_cheap_for_simple=True),
    )

    orchestrator = AdaptiveOrchestrator(
        router=router,
        config=OrchestrationConfig(enabled=True),
    )

    async def direct_turn(p: Any = None) -> str:
        return "Done"

    await orchestrator.orchestrate_turn(
        prompt="Tell me a joke",
        session_id="s-otel",
        direct_turn_fn=direct_turn,
    )

    spans = otel_in_memory.get_finished_spans()
    turn_span = next((s for s in spans if s.name == "yusufclaw.orchestration.turn"), None)
    assert turn_span is not None
    assert "yusufclaw.routing.provider" in turn_span.attributes
    assert turn_span.attributes["yusufclaw.routing.provider"] == "cheap"
    assert turn_span.attributes["yusufclaw.routing.role"] == "default"


@pytest.mark.asyncio
async def test_subagent_runner_routing_span_attributes(
    otel_in_memory: InMemorySpanExporter,
) -> None:
    worker_provider = FakeProvider(default_text="Worker subagent complete.")
    router = ModelRouter(
        providers={"worker": worker_provider, "default": worker_provider},
    )
    router.set_route("specialist", "worker")

    runner = SubagentRunner(
        provider=worker_provider,
        router=router,
    )

    spec = SubagentSpec(
        name="specialist_worker",
        instruction="Do specialized research",
        model_role="specialist",
    )

    res = await runner.run(spec)
    assert res.success is True

    spans = otel_in_memory.get_finished_spans()
    sub_span = next((s for s in spans if s.name == "yusufclaw.orchestration.subagent"), None)
    assert sub_span is not None
    assert sub_span.attributes["yusufclaw.routing.role"] == "specialist"
    assert sub_span.attributes["yusufclaw.routing.provider"] == "worker"
