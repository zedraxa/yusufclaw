"""OpenTelemetry telemetry subpackage for YusufClaw.

Implements enterprise-grade OpenTelemetry instrumentation for distributed tracing and metrics:
- Agent turn duration, execution status, and session/request tracking.
- Model call latency and token consumption attributes (GenAI semantic conventions).
- Tool invocation telemetry (timing, execution status, policy denials).
- Human ActionCard approval wait duration and decision status.
- Safe no-op and offline defaults when OTLP endpoint is unset (zero network calls).
- Optional OTLP gRPC/HTTP exporter integration for production telemetry collectors.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

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
from yusufclaw.telemetry.instrumentation import (
    ApprovalSpanContext,
    ToolSpanContext,
    record_model_response,
    trace_agent_turn,
    trace_approval_wait,
    trace_model_call,
    trace_tool_call,
)
from yusufclaw.telemetry.metrics import (
    MetricsRegistry,
    create_metrics_registry,
    get_metrics,
    reset_metrics,
)

if TYPE_CHECKING:
    from opentelemetry.sdk.metrics.export import MetricReader
    from opentelemetry.sdk.trace.export import SpanExporter

logger = logging.getLogger(__name__)

_INITIALIZED: bool = False
_TRACER_PROVIDER: TracerProvider | None = None
_METER_PROVIDER: MeterProvider | None = None


def setup_telemetry(
    service_name: str = "yusufclaw",
    otlp_endpoint: str | None = None,
    span_exporter: SpanExporter | None = None,
    metric_reader: MetricReader | None = None,
    force_reinit: bool = False,
) -> None:
    """Initialize OpenTelemetry tracer and meter providers.

    Args:
        service_name: The name of the service reporting telemetry.
        otlp_endpoint: Optional OTLP gRPC/HTTP endpoint for exporting spans/metrics.
        span_exporter: Optional custom SpanExporter (e.g. InMemorySpanExporter for testing).
        metric_reader: Optional custom MetricReader (e.g. InMemoryMetricReader for testing).
        force_reinit: If True, re-initialize even if already initialized.
    """
    global _INITIALIZED, _TRACER_PROVIDER, _METER_PROVIDER

    if _INITIALIZED and not force_reinit:
        return

    reset_metrics()

    resource = Resource.create({"service.name": service_name})

    # 1. Trace Provider Setup
    tracer_provider = TracerProvider(resource=resource)
    if span_exporter is not None:
        tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    elif otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
        except ImportError as exc:
            raise ImportError(
                "opentelemetry-exporter-otlp-proto-http is required when otlp_endpoint is configured."
            ) from exc

        endpoint = otlp_endpoint.rstrip("/")
        if not endpoint.endswith("/v1/traces") and "/v1/" not in endpoint:
            endpoint = f"{endpoint}/v1/traces"

        exporter = OTLPSpanExporter(endpoint=endpoint)
        tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
    # Otherwise: no exporter attached (safe, offline, in-memory no-op defaults)

    try:
        otel_trace.set_tracer_provider(tracer_provider)
    except Exception:
        # Ignore warning/error if global provider was already registered
        pass
    _TRACER_PROVIDER = tracer_provider

    # 2. Metric Provider Setup
    readers: list[MetricReader] = []
    if metric_reader is not None:
        readers.append(metric_reader)
    elif otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        except ImportError as exc:
            raise ImportError(
                "opentelemetry-exporter-otlp-proto-http is required when otlp_endpoint is configured."
            ) from exc

        endpoint = otlp_endpoint.rstrip("/")
        if not endpoint.endswith("/v1/metrics") and "/v1/" not in endpoint:
            endpoint = f"{endpoint}/v1/metrics"

        metric_exporter = OTLPMetricExporter(endpoint=endpoint)
        readers.append(PeriodicExportingMetricReader(metric_exporter))

    meter_provider = MeterProvider(resource=resource, metric_readers=readers)
    try:
        otel_metrics.set_meter_provider(meter_provider)
    except Exception:
        pass
    _METER_PROVIDER = meter_provider

    _INITIALIZED = True


def reset_telemetry() -> None:
    """Reset telemetry providers and state (primarily for test isolation)."""
    global _INITIALIZED, _TRACER_PROVIDER, _METER_PROVIDER
    reset_metrics()
    _INITIALIZED = False
    _TRACER_PROVIDER = None
    _METER_PROVIDER = None


def is_telemetry_initialized() -> bool:
    """Check if OpenTelemetry providers have been initialized."""
    return _INITIALIZED


def get_tracer(name: str = "yusufclaw") -> otel_trace.Tracer:
    """Get an OpenTelemetry Tracer instance from active provider or global."""
    if _TRACER_PROVIDER is not None:
        return _TRACER_PROVIDER.get_tracer(name)
    return otel_trace.get_tracer(name)


def get_meter(name: str = "yusufclaw") -> otel_metrics.Meter:
    """Get an OpenTelemetry Meter instance from active provider or global."""
    if _METER_PROVIDER is not None:
        return _METER_PROVIDER.get_meter(name)
    return otel_metrics.get_meter(name)


__all__ = [
    "ATTR_APPROVAL_ID",
    "ATTR_APPROVAL_STATUS",
    "ATTR_APPROVAL_TOOL_NAME",
    "ATTR_GEN_AI_OPERATION_NAME",
    "ATTR_GEN_AI_REQUEST_MAX_TOKENS",
    "ATTR_GEN_AI_REQUEST_MODEL",
    "ATTR_GEN_AI_REQUEST_TEMPERATURE",
    "ATTR_GEN_AI_SYSTEM",
    "ATTR_GEN_AI_USAGE_COMPLETION_TOKENS",
    "ATTR_GEN_AI_USAGE_INPUT_TOKENS",
    "ATTR_GEN_AI_USAGE_OUTPUT_TOKENS",
    "ATTR_GEN_AI_USAGE_PROMPT_TOKENS",
    "ATTR_GEN_AI_USAGE_TOTAL_TOKENS",
    "ATTR_REQUEST_ID",
    "ATTR_SESSION_ID",
    "ATTR_TOOL_CALL_ID",
    "ATTR_TOOL_IS_GUARDED",
    "ATTR_TOOL_NAME",
    "ATTR_TOOL_STATUS",
    "ATTR_TURN_STATUS",
    "ApprovalSpanContext",
    "MetricsRegistry",
    "SPAN_AGENT_TURN",
    "SPAN_APPROVAL_WAIT",
    "SPAN_MODEL_CALL",
    "SPAN_TOOL_EXECUTION",
    "ToolSpanContext",
    "create_metrics_registry",
    "get_meter",
    "get_metrics",
    "get_tracer",
    "is_telemetry_initialized",
    "record_model_response",
    "reset_metrics",
    "reset_telemetry",
    "setup_telemetry",
    "trace_agent_turn",
    "trace_approval_wait",
    "trace_model_call",
    "trace_tool_call",
]
