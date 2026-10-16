"""OpenTelemetry metrics instruments and registry for YusufClaw.

Provides strongly-typed metrics instruments for:
- Agent turn duration & execution counts
- Model provider call latency & token consumption
- Tool invocation durations & status counts
- ActionCard human approval wait times
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.metrics import Counter, Histogram, Meter


@dataclass(frozen=True)
class MetricsRegistry:
    """Collection of OpenTelemetry metric instruments for YusufClaw."""

    turn_duration: Histogram
    turn_counter: Counter
    model_duration: Histogram
    token_counter: Counter
    tool_duration: Histogram
    tool_counter: Counter
    approval_duration: Histogram
    approval_counter: Counter


_GLOBAL_REGISTRY: MetricsRegistry | None = None


def create_metrics_registry(meter: Meter) -> MetricsRegistry:
    """Create a new set of metric instruments bound to the provided Meter."""
    return MetricsRegistry(
        turn_duration=meter.create_histogram(
            name="agent.turn.duration",
            unit="ms",
            description="Duration of agent turns in milliseconds",
        ),
        turn_counter=meter.create_counter(
            name="agent.turns.total",
            unit="1",
            description="Total count of agent turns executed",
        ),
        model_duration=meter.create_histogram(
            name="model.call.duration",
            unit="ms",
            description="Latency of model provider calls in milliseconds",
        ),
        token_counter=meter.create_counter(
            name="model.tokens.total",
            unit="1",
            description="Total tokens consumed by models",
        ),
        tool_duration=meter.create_histogram(
            name="tool.execution.duration",
            unit="ms",
            description="Duration of tool invocations in milliseconds",
        ),
        tool_counter=meter.create_counter(
            name="tool.executions.total",
            unit="1",
            description="Total tool executions",
        ),
        approval_duration=meter.create_histogram(
            name="approval.wait.duration",
            unit="ms",
            description="Wait time for human ActionCard approvals in milliseconds",
        ),
        approval_counter=meter.create_counter(
            name="approval.requests.total",
            unit="1",
            description="Total approval requests processed",
        ),
    )


def get_metrics(meter: Meter | None = None) -> MetricsRegistry:
    """Get the global metrics registry, lazily initializing if needed."""
    global _GLOBAL_REGISTRY
    if meter is not None:
        return create_metrics_registry(meter)

    if _GLOBAL_REGISTRY is None:
        # Import dynamically to avoid circular import
        from yusufclaw.telemetry import get_meter

        _GLOBAL_REGISTRY = create_metrics_registry(get_meter("yusufclaw"))
    return _GLOBAL_REGISTRY


def reset_metrics() -> None:
    """Clear the cached metrics registry singleton."""
    global _GLOBAL_REGISTRY
    _GLOBAL_REGISTRY = None
