"""Context managers and helper functions for tracing and instrumenting agent executions.

Follows OpenTelemetry semantic conventions for:
- Agent turn execution and lifecycle tracking
- Model provider calls, latency measurement, and token accounting
- Tool invocations, policy evaluations, and execution statuses
- ActionCard human approval wait durations
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from opentelemetry import trace

from yusufclaw.telemetry.attributes import (
    ATTR_APPROVAL_ID,
    ATTR_APPROVAL_STATUS,
    ATTR_APPROVAL_TOOL_NAME,
    ATTR_GEN_AI_OPERATION_NAME,
    ATTR_GEN_AI_REQUEST_MAX_TOKENS,
    ATTR_GEN_AI_REQUEST_MODEL,
    ATTR_GEN_AI_REQUEST_TEMPERATURE,
    ATTR_GEN_AI_SYSTEM,
    ATTR_GEN_AI_USAGE_COMPLETION_TOKENS,
    ATTR_GEN_AI_USAGE_INPUT_TOKENS,
    ATTR_GEN_AI_USAGE_OUTPUT_TOKENS,
    ATTR_GEN_AI_USAGE_PROMPT_TOKENS,
    ATTR_GEN_AI_USAGE_TOTAL_TOKENS,
    ATTR_REQUEST_ID,
    ATTR_ROUTING_PROVIDER,
    ATTR_ROUTING_REASON,
    ATTR_ROUTING_ROLE,
    ATTR_ROUTING_TARGET,
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
)
from yusufclaw.telemetry.metrics import get_metrics


def _get_active_tracer(tracer: trace.Tracer | None = None) -> trace.Tracer:
    """Return provided tracer or default singleton tracer from get_tracer."""
    if tracer is not None:
        return tracer
    from yusufclaw.telemetry import get_tracer

    return get_tracer("yusufclaw")


def _resolve_model_metadata(provider: Any) -> tuple[str, str]:
    """Resolve (system_name, model_name) from provider instance."""
    if hasattr(provider, "get_provider") and callable(provider.get_provider):
        try:
            routed = provider.get_provider()
            if routed is not None and routed is not provider:
                return _resolve_model_metadata(routed)
        except Exception:
            pass

    system = getattr(provider, "provider_name", None)
    if not system:
        cls_name = provider.__class__.__name__.lower()
        if cls_name.startswith("fake"):
            system = "fake"
        else:
            system = cls_name.removesuffix("provider") if cls_name.endswith("provider") else cls_name

    model = (
        getattr(provider, "model", None)
        or getattr(provider, "bedrock_model_id", None)
        or getattr(provider, "ollama_model", None)
        or system
    )
    return str(system), str(model)


# ---------------------------------------------------------------------------
# 1. Agent Turn Tracing
# ---------------------------------------------------------------------------
@asynccontextmanager
async def trace_agent_turn(
    session_id: str = "default",
    request_id: str | None = None,
    tracer: trace.Tracer | None = None,
) -> AsyncIterator[trace.Span]:
    """Async context manager tracing the overall agent turn."""
    tr = _get_active_tracer(tracer)
    start_time = time.perf_counter()

    with tr.start_as_current_span(SPAN_AGENT_TURN) as span:
        if span.is_recording():
            span.set_attribute(ATTR_SESSION_ID, session_id)
            if request_id:
                span.set_attribute(ATTR_REQUEST_ID, request_id)
            span.set_attribute(ATTR_TURN_STATUS, "running")

        status = "success"
        try:
            yield span
            if span.is_recording():
                span.set_attribute(ATTR_TURN_STATUS, "success")
        except Exception as exc:
            status = "error"
            if span.is_recording():
                span.set_attribute(ATTR_TURN_STATUS, "error")
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
            raise
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            metrics = get_metrics()
            attrs = {
                ATTR_SESSION_ID: session_id,
                ATTR_TURN_STATUS: status,
            }
            metrics.turn_duration.record(duration_ms, attrs)
            metrics.turn_counter.add(1, attrs)


# ---------------------------------------------------------------------------
# 2. Model Provider Call Tracing & Token Accounting
# ---------------------------------------------------------------------------
@asynccontextmanager
async def trace_model_call(
    provider: Any,
    request: Any,
    tracer: trace.Tracer | None = None,
    role: str | None = None,
) -> AsyncIterator[trace.Span]:
    """Async context manager tracing a single model provider completion call."""
    tr = _get_active_tracer(tracer)
    start_time = time.perf_counter()
    system_name, model_name = _resolve_model_metadata(provider)

    target_provider = provider
    if hasattr(provider, "get_provider") and callable(provider.get_provider):
        try:
            routed = provider.get_provider(role or "default")
            if routed is not None and routed is not provider:
                target_provider = routed
        except Exception:
            pass

    with tr.start_as_current_span(SPAN_MODEL_CALL) as span:
        if span.is_recording():
            span.set_attribute(ATTR_GEN_AI_SYSTEM, system_name)
            span.set_attribute(ATTR_GEN_AI_REQUEST_MODEL, model_name)
            span.set_attribute(ATTR_GEN_AI_OPERATION_NAME, "chat")
            effective_role = (
                role
                or getattr(target_provider, "_routing_role", None)
                or getattr(target_provider, "routing_role", None)
                or getattr(provider, "_routing_role", None)
                or getattr(provider, "routing_role", None)
            )
            if effective_role:
                span.set_attribute(ATTR_ROUTING_ROLE, str(effective_role))

            routing_provider = (
                getattr(target_provider, "_routing_provider", None)
                or getattr(target_provider, "routing_provider", None)
                or getattr(provider, "_routing_provider", None)
                or getattr(provider, "routing_provider", None)
            )
            if routing_provider:
                span.set_attribute(ATTR_ROUTING_PROVIDER, str(routing_provider))

            routing_target = (
                getattr(target_provider, "_routing_target", None)
                or getattr(target_provider, "routing_target", None)
                or getattr(provider, "_routing_target", None)
                or getattr(provider, "routing_target", None)
            )
            if routing_target:
                span.set_attribute(ATTR_ROUTING_TARGET, str(routing_target))

            routing_reason = (
                getattr(target_provider, "_routing_reason", None)
                or getattr(target_provider, "routing_reason", None)
                or getattr(provider, "_routing_reason", None)
                or getattr(provider, "routing_reason", None)
            )
            if routing_reason:
                span.set_attribute(ATTR_ROUTING_REASON, str(routing_reason))

            max_tokens = getattr(request, "max_tokens", None)
            if max_tokens is not None:
                span.set_attribute(ATTR_GEN_AI_REQUEST_MAX_TOKENS, max_tokens)
            temperature = getattr(request, "temperature", None)
            if temperature is not None:
                span.set_attribute(ATTR_GEN_AI_REQUEST_TEMPERATURE, float(temperature))

        status = "success"
        try:
            yield span
        except Exception as exc:
            status = "error"
            if span.is_recording():
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
            raise
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            metrics = get_metrics()
            attrs = {
                ATTR_GEN_AI_SYSTEM: system_name,
                ATTR_GEN_AI_REQUEST_MODEL: model_name,
                "status": status,
            }
            metrics.model_duration.record(duration_ms, attrs)


def record_model_response(
    span: trace.Span,
    response: Any,
    provider: Any = None,
) -> None:
    """Record token usage and model response metadata onto span and metrics."""
    if response is None:
        return

    usage = getattr(response, "usage", {}) or {}
    prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))

    _, model_name = _resolve_model_metadata(provider) if provider else ("unknown", "unknown")

    if span.is_recording():
        if prompt_tokens:
            span.set_attribute(ATTR_GEN_AI_USAGE_PROMPT_TOKENS, prompt_tokens)
            span.set_attribute(ATTR_GEN_AI_USAGE_INPUT_TOKENS, prompt_tokens)
        if completion_tokens:
            span.set_attribute(ATTR_GEN_AI_USAGE_COMPLETION_TOKENS, completion_tokens)
            span.set_attribute(ATTR_GEN_AI_USAGE_OUTPUT_TOKENS, completion_tokens)
        if total_tokens:
            span.set_attribute(ATTR_GEN_AI_USAGE_TOTAL_TOKENS, total_tokens)

    metrics = get_metrics()
    if prompt_tokens:
        metrics.token_counter.add(prompt_tokens, {"token_type": "prompt", "model": model_name})
    if completion_tokens:
        metrics.token_counter.add(completion_tokens, {"token_type": "completion", "model": model_name})
    if total_tokens:
        metrics.token_counter.add(total_tokens, {"token_type": "total", "model": model_name})


# ---------------------------------------------------------------------------
# 3. Tool Invocation Tracing
# ---------------------------------------------------------------------------
class ToolSpanContext:
    """Mutable context passed to the caller during tool execution tracing."""

    def __init__(self, span: trace.Span, name: str, is_guarded: bool) -> None:
        self.span = span
        self.name = name
        self.is_guarded = is_guarded
        self.status: str = "success"

    def set_status(self, status: str) -> None:
        """Update execution status on both the wrapper context and span attribute."""
        self.status = status
        if self.span.is_recording():
            self.span.set_attribute(ATTR_TOOL_STATUS, status)

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a custom attribute on the active span."""
        if self.span.is_recording():
            self.span.set_attribute(key, value)


@asynccontextmanager
async def trace_tool_call(
    name: str,
    call_id: str,
    args: dict[str, Any] | None = None,
    is_guarded: bool = False,
    tracer: trace.Tracer | None = None,
) -> AsyncIterator[ToolSpanContext]:
    """Async context manager tracing tool invocation timing and execution status."""
    tr = _get_active_tracer(tracer)
    start_time = time.perf_counter()

    with tr.start_as_current_span(SPAN_TOOL_EXECUTION) as span:
        if span.is_recording():
            span.set_attribute(ATTR_TOOL_NAME, name)
            span.set_attribute(ATTR_TOOL_CALL_ID, call_id)
            span.set_attribute(ATTR_TOOL_IS_GUARDED, is_guarded)
            span.set_attribute(ATTR_TOOL_STATUS, "running")

        ctx = ToolSpanContext(span=span, name=name, is_guarded=is_guarded)
        try:
            yield ctx
        except Exception as exc:
            ctx.set_status("error")
            if span.is_recording():
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
            raise
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            # Ensure the span attribute reflects the final context status
            if span.is_recording() and ctx.status != "running":
                span.set_attribute(ATTR_TOOL_STATUS, ctx.status)

            metrics = get_metrics()
            attrs = {
                ATTR_TOOL_NAME: name,
                ATTR_TOOL_STATUS: ctx.status,
                ATTR_TOOL_IS_GUARDED: str(is_guarded),
            }
            metrics.tool_duration.record(duration_ms, attrs)
            metrics.tool_counter.add(1, attrs)


# ---------------------------------------------------------------------------
# 4. ActionCard Approval Wait Tracing
# ---------------------------------------------------------------------------
class ApprovalSpanContext:
    """Mutable context passed to the caller during human approval tracing."""

    def __init__(self, span: trace.Span, tool_name: str, approval_id: str | None) -> None:
        self.span = span
        self.tool_name = tool_name
        self.approval_id = approval_id
        self.status: str = "pending"

    def set_status(self, status: str) -> None:
        """Update approval status on both wrapper context and span attribute."""
        self.status = status
        if self.span.is_recording():
            self.span.set_attribute(ATTR_APPROVAL_STATUS, status)

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a custom attribute on the active span."""
        if self.span.is_recording():
            self.span.set_attribute(key, value)


@asynccontextmanager
async def trace_approval_wait(
    action_card: Any,
    tool_name: str,
    tracer: trace.Tracer | None = None,
) -> AsyncIterator[ApprovalSpanContext]:
    """Async context manager tracing human-in-the-loop ActionCard approval latency."""
    tr = _get_active_tracer(tracer)
    start_time = time.perf_counter()
    approval_id = getattr(action_card, "approval_id", None) or getattr(action_card, "id", None)

    with tr.start_as_current_span(SPAN_APPROVAL_WAIT) as span:
        if span.is_recording():
            span.set_attribute(ATTR_APPROVAL_TOOL_NAME, tool_name)
            if approval_id:
                span.set_attribute(ATTR_APPROVAL_ID, str(approval_id))
            span.set_attribute(ATTR_APPROVAL_STATUS, "pending")

        ctx = ApprovalSpanContext(
            span=span,
            tool_name=tool_name,
            approval_id=str(approval_id) if approval_id else None,
        )
        try:
            yield ctx
        except Exception as exc:
            ctx.set_status("error")
            if span.is_recording():
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
            raise
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            if span.is_recording() and ctx.status != "pending":
                span.set_attribute(ATTR_APPROVAL_STATUS, ctx.status)

            metrics = get_metrics()
            attrs = {
                ATTR_APPROVAL_TOOL_NAME: tool_name,
                ATTR_APPROVAL_STATUS: ctx.status,
            }
            metrics.approval_duration.record(duration_ms, attrs)
            metrics.approval_counter.add(1, attrs)


# ---------------------------------------------------------------------------
# 5. Model Routing Telemetry
# ---------------------------------------------------------------------------
def record_routing_decision(
    span: trace.Span | None = None,
    *,
    role: str,
    provider_name: str,
    target: str | None = None,
    reason: str | None = None,
) -> None:
    """Record model routing decision attributes on the given or current span."""
    active_span = span or trace.get_current_span()
    if active_span is not None and active_span.is_recording():
        active_span.set_attribute(ATTR_ROUTING_ROLE, role)
        active_span.set_attribute(ATTR_ROUTING_PROVIDER, provider_name)
        if target:
            active_span.set_attribute(ATTR_ROUTING_TARGET, target)
        if reason:
            active_span.set_attribute(ATTR_ROUTING_REASON, reason)

