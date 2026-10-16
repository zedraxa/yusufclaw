"""Telemetry subpackage for YusufClaw.

Implements OpenTelemetry instrumentation for distributed tracing and metrics:
- Agent turn duration, model latency, and token consumption tracking.
- Tool invocation telemetry (execution time, failure status, approval wait times).
- Structured traces across gateway requests, agent reasoning loops, and external calls.
"""

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

_INITIALIZED = False


def setup_telemetry(
    service_name: str = "yusufclaw",
    otlp_endpoint: str | None = None,
) -> None:
    """Initialize OpenTelemetry tracer and meter providers.

    Args:
        service_name: The name of the service reporting telemetry.
        otlp_endpoint: Optional OTLP gRPC/HTTP endpoint for exporting spans/metrics.
    """
    global _INITIALIZED
    if _INITIALIZED:
        return

    resource = Resource.create({"service.name": service_name})

    tracer_provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(resource=resource)
    metrics.set_meter_provider(meter_provider)

    _INITIALIZED = True


def get_tracer(name: str = "yusufclaw") -> trace.Tracer:
    """Get an OpenTelemetry Tracer instance."""
    return trace.get_tracer(name)


def get_meter(name: str = "yusufclaw") -> metrics.Meter:
    """Get an OpenTelemetry Meter instance."""
    return metrics.get_meter(name)


__all__ = ["setup_telemetry", "get_tracer", "get_meter"]
