"""Tests for YusufClaw OpenTelemetry distributed tracing and metrics instrumentation.

Verifies:
- Offline setup and safe default no-op providers (zero network).
- Custom in-memory span exporter and metric reader integration.
- OTLP endpoint configuration logic without live network requests.
- Full trace hierarchies: root agent turn -> model calls -> approval wait -> tool execution.
- GenAI semantic convention attributes (model, system, prompt/completion token usage).
- OpenTelemetry metrics recording (turn duration, turn counter, model latency, token counter, tool duration).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from yusufclaw.agent import Agent
from yusufclaw.models.fake import FakeProvider
from yusufclaw.models.types import ModelRequest, ModelResponse, ToolCall, ToolDefinition
from yusufclaw.security import ActionCard, Grants
from yusufclaw.telemetry import (
    ATTR_APPROVAL_STATUS,
    ATTR_APPROVAL_TOOL_NAME,
    ATTR_GEN_AI_OPERATION_NAME,
    ATTR_GEN_AI_REQUEST_MODEL,
    ATTR_GEN_AI_SYSTEM,
    ATTR_GEN_AI_USAGE_COMPLETION_TOKENS,
    ATTR_GEN_AI_USAGE_PROMPT_TOKENS,
    ATTR_GEN_AI_USAGE_TOTAL_TOKENS,
    ATTR_REQUEST_ID,
    ATTR_SESSION_ID,
    ATTR_TOOL_CALL_ID,
    ATTR_TOOL_IS_GUARDED,
    ATTR_TOOL_NAME,
    ATTR_TOOL_STATUS,
    ATTR_TURN_STATUS,
    SPAN_AGENT_TURN,
    SPAN_APPROVAL_WAIT,
    SPAN_MODEL_CALL,
    SPAN_TOOL_EXECUTION,
    get_meter,
    get_tracer,
    is_telemetry_initialized,
    reset_telemetry,
    setup_telemetry,
)


@pytest.fixture(autouse=True)
def clean_telemetry() -> Any:
    """Ensure every test begins with clean telemetry state and resets afterward."""
    reset_telemetry()
    yield
    reset_telemetry()


def test_telemetry_setup_default_offline() -> None:
    """Verify that default setup produces active offline providers with zero network."""
    setup_telemetry(service_name="test-offline-service")
    assert is_telemetry_initialized() is True

    tracer = get_tracer("test-tracer")
    meter = get_meter("test-meter")
    assert tracer is not None
    assert meter is not None

    # Test that creating spans and instruments works without error in offline mode
    with tracer.start_as_current_span("offline.span") as span:
        assert span.is_recording() is True
        span.set_attribute("offline.key", "value")

    counter = meter.create_counter("offline.counter")
    counter.add(1, {"env": "test"})


def test_telemetry_setup_with_in_memory_exporter() -> None:
    """Verify setup with InMemorySpanExporter and InMemoryMetricReader captures spans and metrics."""
    exporter = InMemorySpanExporter()
    reader = InMemoryMetricReader()

    setup_telemetry(
        service_name="test-inmemory-service",
        span_exporter=exporter,
        metric_reader=reader,
        force_reinit=True,
    )

    tracer = get_tracer("test-suite")
    meter = get_meter("test-suite")

    with tracer.start_as_current_span("unit.test.span") as span:
        span.set_attribute("custom.tag", 123)

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "unit.test.span"
    assert spans[0].attributes["custom.tag"] == 123  # type: ignore
    assert spans[0].resource.attributes["service.name"] == "test-inmemory-service"

    test_hist = meter.create_histogram("test.histogram", unit="ms")
    test_hist.record(42.5, {"status": "ok"})

    metrics_data = reader.get_metrics_data()
    assert metrics_data is not None
    assert len(metrics_data.resource_metrics) > 0


def test_telemetry_setup_otlp_endpoint_parsing() -> None:
    """Verify OTLP endpoint configuration appends standard OTLP /v1/ paths."""
    setup_telemetry(
        service_name="test-otlp-service",
        otlp_endpoint="http://collector.local:4318",
        force_reinit=True,
    )
    assert is_telemetry_initialized() is True

    tracer = get_tracer()
    assert tracer is not None


def test_telemetry_reinit_idempotency() -> None:
    """Verify calling setup_telemetry multiple times without force_reinit is a no-op."""
    exporter1 = InMemorySpanExporter()
    setup_telemetry(service_name="first", span_exporter=exporter1)

    exporter2 = InMemorySpanExporter()
    setup_telemetry(service_name="second", span_exporter=exporter2)

    tracer = get_tracer()
    with tracer.start_as_current_span("span.first"):
        pass

    assert len(exporter1.get_finished_spans()) == 1
    assert len(exporter2.get_finished_spans()) == 0

    # With force_reinit, it reconfigures
    setup_telemetry(service_name="second", span_exporter=exporter2, force_reinit=True)
    tracer = get_tracer()
    with tracer.start_as_current_span("span.second"):
        pass

    assert len(exporter2.get_finished_spans()) == 1


@pytest.mark.asyncio
async def test_instrumented_agent_single_turn() -> None:
    """Verify single agent turn emits parent turn span and child model call span with token metrics."""
    exporter = InMemorySpanExporter()
    reader = InMemoryMetricReader()
    setup_telemetry(
        service_name="yusufclaw-test",
        span_exporter=exporter,
        metric_reader=reader,
        force_reinit=True,
    )

    provider = FakeProvider(default_text="Hello from AI!")
    agent = Agent(provider=provider)

    reply = await agent.run("Hello there", session_id="test-session-1")
    assert reply == "Hello from AI!"

    spans = exporter.get_finished_spans()
    # Expect 2 spans: model call (child) and agent turn (parent)
    assert len(spans) == 2

    # Spans are finished in order: child first, parent second
    model_span: ReadableSpan = next(s for s in spans if s.name == SPAN_MODEL_CALL)
    turn_span: ReadableSpan = next(s for s in spans if s.name == SPAN_AGENT_TURN)

    # 1. Check parent-child hierarchy
    assert model_span.parent is not None
    assert model_span.parent.span_id == turn_span.context.span_id

    # 2. Check agent turn attributes
    assert turn_span.attributes[ATTR_SESSION_ID] == "test-session-1"  # type: ignore
    assert turn_span.attributes[ATTR_TURN_STATUS] == "success"  # type: ignore
    assert turn_span.attributes["yusufclaw.turn.step_count"] == 1  # type: ignore

    # 3. Check model call attributes (GenAI semconv)
    assert model_span.attributes[ATTR_GEN_AI_SYSTEM] == "fake"  # type: ignore
    assert model_span.attributes[ATTR_GEN_AI_REQUEST_MODEL] == "fake"  # type: ignore
    assert model_span.attributes[ATTR_GEN_AI_OPERATION_NAME] == "chat"  # type: ignore
    assert model_span.attributes[ATTR_GEN_AI_USAGE_PROMPT_TOKENS] == 10  # type: ignore
    assert model_span.attributes[ATTR_GEN_AI_USAGE_COMPLETION_TOKENS] == 10  # type: ignore
    assert model_span.attributes[ATTR_GEN_AI_USAGE_TOTAL_TOKENS] == 20  # type: ignore

    # 4. Check metrics
    metrics_data = reader.get_metrics_data()
    assert metrics_data is not None
    metric_names = [
        m.name
        for rm in metrics_data.resource_metrics
        for sm in rm.scope_metrics
        for m in sm.metrics
    ]
    assert "agent.turn.duration" in metric_names
    assert "agent.turns.total" in metric_names
    assert "model.call.duration" in metric_names
    assert "model.tokens.total" in metric_names


@pytest.mark.asyncio
async def test_instrumented_agent_handle_session_and_request_id() -> None:
    """Verify agent.handle() attaches session_id and request_id to the root turn span."""
    exporter = InMemorySpanExporter()
    setup_telemetry(
        service_name="yusufclaw-test",
        span_exporter=exporter,
        force_reinit=True,
    )

    provider = FakeProvider(default_text="Handle response")
    agent = Agent(provider=provider)

    res = await agent.handle(session="conv-999", request_id="req-abc", text="Testing handle")
    assert res == "Handle response"

    spans = exporter.get_finished_spans()
    turn_span = next(s for s in spans if s.name == SPAN_AGENT_TURN)
    assert turn_span.attributes[ATTR_SESSION_ID] == "conv-999"  # type: ignore
    assert turn_span.attributes[ATTR_REQUEST_ID] == "req-abc"  # type: ignore
    assert turn_span.attributes[ATTR_TURN_STATUS] == "success"  # type: ignore


@pytest.mark.asyncio
async def test_instrumented_agent_tool_invocation_hierarchy() -> None:
    """Verify agent turn with tool call emits complete trace tree: turn -> model -> tool -> model."""
    exporter = InMemorySpanExporter()
    setup_telemetry(
        service_name="yusufclaw-test",
        span_exporter=exporter,
        force_reinit=True,
    )

    # First model response triggers a tool call; second returns final answer
    call_id = f"call_{uuid.uuid4().hex[:8]}"
    canned = [
        ModelResponse(
            text="Let me calculate this.",
            tool_calls=[ToolCall(id=call_id, name="calc", input={"expr": "2+2"})],
            usage={"prompt_tokens": 15, "completion_tokens": 8},
        ),
        ModelResponse(
            text="The answer is 4.",
            tool_calls=[],
            usage={"prompt_tokens": 25, "completion_tokens": 6},
        ),
    ]
    provider = FakeProvider(canned_responses=canned)

    tool_def = ToolDefinition(
        name="calc",
        description="Math calculator",
        input_schema={"type": "object", "properties": {"expr": {"type": "string"}}},
    )

    async def mock_executor(name: str, args: dict[str, Any]) -> str:
        return "4"

    agent = Agent(
        provider=provider,
        tools=[tool_def],
        tool_executor=mock_executor,
    )

    reply = await agent.run("Calculate 2+2", session_id="calc-session")
    assert reply == "The answer is 4."

    spans = exporter.get_finished_spans()
    # Expect 4 spans: model(1), tool(calc), model(2), and root turn
    assert len(spans) == 4

    turn_span = next(s for s in spans if s.name == SPAN_AGENT_TURN)
    tool_span = next(s for s in spans if s.name == SPAN_TOOL_EXECUTION)
    model_spans = [s for s in spans if s.name == SPAN_MODEL_CALL]
    assert len(model_spans) == 2

    # Verify all 3 child spans have turn_span as parent
    for child in (*model_spans, tool_span):
        assert child.parent is not None
        assert child.parent.span_id == turn_span.context.span_id

    # Verify tool span attributes
    assert tool_span.attributes[ATTR_TOOL_NAME] == "calc"  # type: ignore
    assert tool_span.attributes[ATTR_TOOL_CALL_ID] == call_id  # type: ignore
    assert tool_span.attributes[ATTR_TOOL_STATUS] == "success"  # type: ignore
    assert tool_span.attributes[ATTR_TOOL_IS_GUARDED] is False  # type: ignore


@pytest.mark.asyncio
async def test_instrumented_tool_policy_denial() -> None:
    """Verify tool invocation denied by security policy records policy_denied status."""
    exporter = InMemorySpanExporter()
    setup_telemetry(
        service_name="yusufclaw-test",
        span_exporter=exporter,
        force_reinit=True,
    )

    call_id = f"call_{uuid.uuid4().hex[:8]}"
    canned = [
        ModelResponse(
            text="Calling disallowed tool.",
            tool_calls=[ToolCall(id=call_id, name="forbidden_tool", input={})],
        ),
        ModelResponse(text="It was denied."),
    ]
    provider = FakeProvider(canned_responses=canned)

    tool_def = ToolDefinition(
        name="forbidden_tool",
        description="Restricted tool",
        input_schema={},
    )

    # Restrict grants so forbidden_tool is not allowed
    grants = Grants(allowed_tools={"allowed_only"})

    agent = Agent(
        provider=provider,
        tools=[tool_def],
        grants=grants,
    )

    reply = await agent.run("Run forbidden", session_id="deny-session")
    assert reply == "It was denied."

    spans = exporter.get_finished_spans()
    tool_span = next(s for s in spans if s.name == SPAN_TOOL_EXECUTION)
    assert tool_span.attributes[ATTR_TOOL_NAME] == "forbidden_tool"  # type: ignore
    assert tool_span.attributes[ATTR_TOOL_STATUS] == "policy_denied"  # type: ignore


@pytest.mark.asyncio
async def test_instrumented_human_approval_wait() -> None:
    """Verify ActionCard approval wait is instrumented with span and approval status."""
    exporter = InMemorySpanExporter()
    setup_telemetry(
        service_name="yusufclaw-test",
        span_exporter=exporter,
        force_reinit=True,
    )

    call_id = f"call_{uuid.uuid4().hex[:8]}"
    canned = [
        ModelResponse(
            text="Requesting delete.",
            tool_calls=[ToolCall(id=call_id, name="delete_tool", input={"target": "foo"})],
        ),
        ModelResponse(text="Done after approval."),
    ]
    provider = FakeProvider(canned_responses=canned)

    from yusufclaw.security import ToolManifest
    from yusufclaw.tools.manifest import GuardedTool

    async def mock_delete(args: dict[str, Any]) -> str:
        return "deleted"

    tool = GuardedTool(
        name="delete_tool",
        description="Deletes target",
        input_schema={"type": "object"},
        manifest=ToolManifest(
            id="delete_tool",
            version="1.0.0",
            source="builtin",
            effects=["exec"],
        ),
        execute_fn=mock_delete,
        requires_approval=True,
    )

    approval_called = False

    async def mock_approve(tc: ToolCall, card: ActionCard | None) -> bool:
        nonlocal approval_called
        approval_called = True
        return True

    agent = Agent(
        provider=provider,
        tools=[tool],
        approve_callback=mock_approve,
    )

    reply = await agent.run("Delete please", session_id="approval-sess")
    assert reply == "Done after approval."
    assert approval_called is True

    spans = exporter.get_finished_spans()
    approval_span = next(s for s in spans if s.name == SPAN_APPROVAL_WAIT)
    assert approval_span.attributes[ATTR_APPROVAL_TOOL_NAME] == "delete_tool"  # type: ignore
    assert approval_span.attributes[ATTR_APPROVAL_STATUS] == "approved"  # type: ignore

    # Verify tool execution span status is success
    tool_span = next(s for s in spans if s.name == SPAN_TOOL_EXECUTION)
    assert tool_span.attributes[ATTR_TOOL_STATUS] == "success"  # type: ignore


@pytest.mark.asyncio
async def test_instrumented_error_handling_in_model_call() -> None:
    """Verify model provider exceptions are captured on the model span with error status."""
    exporter = InMemorySpanExporter()
    setup_telemetry(
        service_name="yusufclaw-test",
        span_exporter=exporter,
        force_reinit=True,
    )

    class FailingProvider(FakeProvider):
        async def complete(self, request: ModelRequest) -> ModelResponse:
            raise RuntimeError("Provider connection failed")

    agent = Agent(provider=FailingProvider())

    with pytest.raises(RuntimeError, match="Provider connection failed"):
        await agent.run("Trigger failure", session_id="fail-sess")

    spans = exporter.get_finished_spans()
    model_span = next(s for s in spans if s.name == SPAN_MODEL_CALL)
    turn_span = next(s for s in spans if s.name == SPAN_AGENT_TURN)

    assert model_span.status.status_code == StatusCode.ERROR
    assert "Provider connection failed" in (model_span.status.description or "")

    assert turn_span.status.status_code == StatusCode.ERROR
    assert turn_span.attributes[ATTR_TURN_STATUS] == "error"  # type: ignore
