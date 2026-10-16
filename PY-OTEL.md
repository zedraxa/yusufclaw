# YusufClaw Python: OpenTelemetry Tracing & Metrics (Layer 8 / P1 OTel)

This document details the OpenTelemetry instrumentation and wiring for YusufClaw Python on branch `py-otel`. It fulfills Layer 8 ("Verification & Governance: OpenTelemetry tracing (tokens, latency, errors per agent)") of `docs/ARCHITECTURE-v2.md`.

---

## 1. Architectural Summary & Scope Boundaries

### Non-Interference Guarantee
- **Untouched Subsystems**: As instructed, `src/yusufclaw/gateway`, `src/yusufclaw/security`, `src/yusufclaw/tools`, `src/yusufclaw/memory`, `src/yusufclaw/skills`, `src/yusufclaw/routines`, and `packages/` (TypeScript) were **not modified**.
- **Minimal Call-Site Instrumentation**: Only `src/yusufclaw/agent.py` was instrumented using clean, non-intrusive async context manager wrappers (`trace_agent_turn`, `trace_model_call`, `trace_tool_call`, `trace_approval_wait`). No existing agent semantics, security policies, memory assembly, or error handling behaviors were altered.
- **Offline & Zero Cloud Dependency**: Default telemetry setup is 100% offline and crash-safe. When `otlp_endpoint` is unset (the default), no network exporters are configured. All tests execute completely offline without outbound sockets or external collector requirements.

---

## 2. Telemetry Architecture (`src/yusufclaw/telemetry/`)

```text
src/yusufclaw/telemetry/
├── __init__.py          # setup_telemetry, reset_telemetry, get_tracer, get_meter
├── attributes.py        # GenAI semantic conventions & YusufClaw span/attribute constants
├── instrumentation.py   # Async context managers: trace_agent_turn, trace_model_call, etc.
└── metrics.py           # MetricsRegistry: histograms & counters for turns, models, tools, approvals
```

### 2.1 `src/yusufclaw/telemetry/attributes.py`
Standardizes span names and attribute keys following the latest OpenTelemetry GenAI Semantic Conventions (`gen_ai.*`) and YusufClaw domain conventions:

- **Span Names**:
  - `SPAN_AGENT_TURN = "yusufclaw.agent.turn"`
  - `SPAN_MODEL_CALL = "yusufclaw.model.complete"`
  - `SPAN_TOOL_EXECUTION = "yusufclaw.tool.execute"`
  - `SPAN_APPROVAL_WAIT = "yusufclaw.approval.wait"`

- **GenAI Semantic Conventions**:
  - `gen_ai.system`: Provider engine (e.g., `"openai"`, `"bedrock"`, `"ollama"`, `"fake"`).
  - `gen_ai.request.model`: Underlying model ID (e.g., `"gpt-4o-mini"`, `"llama3.2"`, etc.).
  - `gen_ai.operation.name`: Operation category (`"chat"`).
  - `gen_ai.usage.prompt_tokens` & `gen_ai.usage.input_tokens`: Input tokens.
  - `gen_ai.usage.completion_tokens` & `gen_ai.usage.output_tokens`: Generated tokens.
  - `gen_ai.usage.total_tokens`: Total turn token expenditure.
  - `gen_ai.request.max_tokens` & `gen_ai.request.temperature`: Execution hyperparameters.

- **Domain Attributes**:
  - `yusufclaw.session_id` & `yusufclaw.request_id`: Multi-turn session and request provenance.
  - `yusufclaw.turn.step_count`: Number of iterations executed within the LangGraph agent loop.
  - `yusufclaw.turn.status`: Status of the turn (`"success"` | `"error"`).
  - `yusufclaw.tool.name`, `yusufclaw.tool.call_id`, `yusufclaw.tool.is_guarded`.
  - `yusufclaw.tool.status`: Tool outcome (`"success"` | `"error"` | `"policy_denied"` | `"approval_denied"`).
  - `yusufclaw.approval.id`, `yusufclaw.approval.tool_name`, `yusufclaw.approval.status`: Human approval state (`"approved"` | `"denied"` | `"error"`).

---

### 2.2 `src/yusufclaw/telemetry/metrics.py`
Defines the `MetricsRegistry` providing strongly-typed OpenTelemetry metrics instruments:

| Metric Name | Instrument | Unit | Description |
|---|---|---|---|
| `agent.turn.duration` | Histogram | `ms` | End-to-end latency of agent turns. |
| `agent.turns.total` | Counter | `1` | Count of agent turns (attributes: `session_id`, `status`). |
| `model.call.duration` | Histogram | `ms` | Latency of individual LLM completions. |
| `model.tokens.total` | Counter | `1` | Tokens consumed (attributes: `token_type`, `model`). |
| `tool.execution.duration` | Histogram | `ms` | Execution duration of tool invocations. |
| `tool.executions.total` | Counter | `1` | Total tool calls (attributes: `tool`, `status`, `is_guarded`). |
| `approval.wait.duration` | Histogram | `ms` | Latency waiting for human ActionCard approvals. |
| `approval.requests.total` | Counter | `1` | Total approval requests (attributes: `tool`, `status`). |

---

### 2.3 `src/yusufclaw/telemetry/instrumentation.py`
Provides reusable asynchronous context managers and helpers:

- **`trace_agent_turn(session_id, request_id)`**:
  Wraps LangGraph execution. Records root span, captures execution duration, updates turn status (`"success"` or `"error"`), attaches exception events on failures, and records turn duration and counter metrics.
- **`trace_model_call(provider, request)`**:
  Wraps `provider.complete(request)`. Records GenAI metadata, measures call duration, captures timeout/runtime exceptions.
- **`record_model_response(span, response, provider)`**:
  Extracts `prompt_tokens`, `completion_tokens`, and `total_tokens` from `ModelResponse.usage`. Populates GenAI span attributes and increments `model.tokens.total` counters.
- **`trace_tool_call(name, call_id, args, is_guarded)`**:
  Yields a `ToolSpanContext` allowing caller to set status (`"success"`, `"policy_denied"`, `"approval_denied"`, `"error"`). Records tool execution duration and counter metrics.
- **`trace_approval_wait(action_card, tool_name)`**:
  Yields an `ApprovalSpanContext` tracking human-in-the-loop wait latency and decision outcome (`"approved"` or `"denied"`).

---

### 2.4 `src/yusufclaw/telemetry/__init__.py`
Exports the public telemetry API:

- **`setup_telemetry(service_name, otlp_endpoint, span_exporter, metric_reader, force_reinit)`**:
  - If `span_exporter` is provided (e.g., `InMemorySpanExporter` in tests), attaches `SimpleSpanProcessor`.
  - If `otlp_endpoint` is configured (e.g., `"http://localhost:4318"`), automatically connects `BatchSpanProcessor(OTLPSpanExporter)` and `PeriodicExportingMetricReader(OTLPMetricExporter)`.
  - If no endpoint or exporter is provided (default), initializes local `TracerProvider` and `MeterProvider` with **no network exporters** (100% offline and safe).
- **`reset_telemetry()`**: Clears cached providers and metric registries for clean test isolation.
- **`get_tracer(name)` & `get_meter(name)`**: Returns tracers and meters bound to the active provider singleton with fallback to global.
- **`is_telemetry_initialized()`**: Query initialization state.

---

## 3. Agent Instrumentation (`src/yusufclaw/agent.py`)

Minimal, elegant call sites were wired into the core LangGraph loop:

1. **Turn Level (`Agent.run_state` and `Agent.handle`)**:
   ```python
   async with trace_agent_turn(session_id=session, request_id=request_id) as turn_span:
       final_state = await self.graph.ainvoke(initial_state)
       if turn_span.is_recording():
           turn_span.set_attribute("yusufclaw.turn.step_count", final_state.get("step_count", 0))
   ```

2. **Model Completion (`model_node`)**:
   ```python
   async with trace_model_call(self.provider, request) as model_span:
       response = await self.provider.complete(request)
       record_model_response(model_span, response, provider=self.provider)
   ```

3. **Tool Invocations & Security Policies (`tool_node`)**:
   - Memory tools and builtin tools wrapped with `async with trace_tool_call(...) as tool_ctx:`.
   - Security policy rejections record `tool_ctx.set_status("policy_denied")`.
   - Unapproved tools record `tool_ctx.set_status("approval_denied")`.
   - Successful executions record `tool_ctx.set_status("success")`.

4. **Human ActionCard Approvals (`tool_node`)**:
   ```python
   async with trace_approval_wait(action_card=action_card, tool_name=name) as app_ctx:
       approved = await self.approve_callback(tc_obj, action_card)
       app_ctx.set_status("approved" if approved else "denied")
   ```

### Distributed Trace Hierarchy

Because OpenTelemetry Python automatically propagates parent context across async coroutines, spans nest cleanly without manual context threading:

```text
yusufclaw.agent.turn (Root Span)
  │  ├── yusufclaw.session_id: "conv-42"
  │  ├── yusufclaw.request_id: "req-1"
  │  ├── yusufclaw.turn.step_count: 2
  │  └── yusufclaw.turn.status: "success"
  │
  ├── yusufclaw.model.complete (Step 1: Planning / Tool Request)
  │     ├── gen_ai.system: "fake"
  │     ├── gen_ai.request.model: "fake"
  │     ├── gen_ai.usage.prompt_tokens: 15
  │     └── gen_ai.usage.completion_tokens: 8
  │
  ├── yusufclaw.approval.wait (Human Decision)
  │     ├── yusufclaw.approval.tool_name: "file_write"
  │     ├── yusufclaw.approval.id: "card_..."
  │     └── yusufclaw.approval.status: "approved"
  │
  ├── yusufclaw.tool.execute (Tool Execution)
  │     ├── yusufclaw.tool.name: "file_write"
  │     ├── yusufclaw.tool.status: "success"
  │     └── yusufclaw.tool.is_guarded: true
  │
  └── yusufclaw.model.complete (Step 2: Final Response Synthesis)
        ├── gen_ai.system: "fake"
        ├── gen_ai.request.model: "fake"
        ├── gen_ai.usage.prompt_tokens: 25
        └── gen_ai.usage.completion_tokens: 6
```

---

## 4. Verification & Test Suite (`tests/python/test_telemetry.py`)

A comprehensive, zero-network test suite covers:
1. `test_telemetry_setup_default_offline`: Verifies no-op/offline defaults without network.
2. `test_telemetry_setup_with_in_memory_exporter`: Captures spans and metrics via `InMemorySpanExporter` and `InMemoryMetricReader`.
3. `test_telemetry_setup_otlp_endpoint_parsing`: Verifies standard OTLP `/v1/` endpoint expansion.
4. `test_telemetry_reinit_idempotency`: Verifies reinitialization guard and `force_reinit=True`.
5. `test_instrumented_agent_single_turn`: End-to-end turn test verifying span hierarchy and GenAI token convention attributes.
6. `test_instrumented_agent_handle_session_and_request_id`: Verifies session and request ID extraction in `Agent.handle()`.
7. `test_instrumented_agent_tool_invocation_hierarchy`: Verifies 4-span nested hierarchy across multi-step tool calling.
8. `test_instrumented_tool_policy_denial`: Verifies security policy denial emits `"policy_denied"` status.
9. `test_instrumented_human_approval_wait`: Verifies ActionCard approval emits approval span with `"approved"` status.
10. `test_instrumented_error_handling_in_model_call`: Verifies exceptions set `StatusCode.ERROR` on model and turn spans.

### Test Execution Results
- **Ruff**: `uv run ruff check .` -> `All checks passed!`
- **Mypy**: `uv run mypy src` -> `Success: no issues found in 49 source files`
- **Pytest**: `uv run pytest` -> **104 passed in 6.27s** (100% green, 0 skipped, 0 network).

---

## 5. Usage & Operations

### Local Offline Execution
No configuration needed. Telemetry runs silently in-memory with zero overhead:
```bash
uv run yusufclaw turn "Hello" --provider fake
```

### Production Telemetry (OTLP Collector / Jaeger / SigNoz)
Configure standard environment variables:
```bash
export OTEL_SERVICE_NAME="yusufclaw"
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318"
uv run yusufclaw web
```
The FastAPI web gateway automatically initializes OTLP export during lifespan startup.
