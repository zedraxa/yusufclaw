# YusufClaw Adaptive Model Routing System (PY-ROUTING)

## 1. Executive Summary & Architectural Positioning

The `yusufclaw.models.router` module implements the Layer 2 Meta-orchestrator model routing specification defined in `docs/ARCHITECTURE-v2.md`:
> *"Model routing: strong model for planning/synthesis, cheap/local (Ollama) for classify/search/transform."*

Prior to this subsystem, the orchestration pipeline (`PY-ORCH.md`) and agent loop always bound to a single uniform `ModelProvider`. The adaptive model routing architecture introduces a decoupled, role-driven, cost-aware dispatch engine that directs discrete cognitive workloads to the optimal provider tier:
- **Strong Model Tier** (e.g., AWS Bedrock Claude 3.5 Sonnet / OpenAI GPT-4o): Deployed for high-judgment, complex reasoning tasks including plan generation, synthesis, verifier judge assessments, and failed-step retry revisions.
- **Cheap / Local Tier** (e.g., local Ollama Llama 3.2 / GPT-4o-mini): Deployed for high-frequency, low-latency utility tasks including model-assisted task classification, heuristic assists, text transformations, search query refinements, and simple direct conversational turns.
- **Single-Provider Fallback Guarantee**: If only a single provider is configured (e.g. `FakeProvider` during test runs or CI), all role queries automatically resolve to that sole instance, ensuring 100% offline determinism and zero network/credential dependencies.

---

## 2. Core Components

### 2.1 Functional Roles (`ModelRole`)

Defined in `src/yusufclaw/models/router.py`:
```python
class ModelRole(StrEnum):
    PLANNER = "planner"
    SYNTHESIS = "synthesis"
    JUDGE = "judge"
    CLASSIFY = "classify"
    SEARCH = "search"
    TRANSFORM = "transform"
    HEURISTIC_ASSIST = "heuristic-assist"
    DEFAULT = "default"
    STRONG = "strong"
    CHEAP = "cheap"
```

### 2.2 Routing Configuration (`RoutingConfig`)

The `RoutingConfig` model governs role-to-provider mappings and operational thresholds:
```python
class RoutingConfig(BaseModel):
    enabled: bool = False
    default_provider: str = "default"
    strong_provider: str = "strong"
    cheap_provider: str = "cheap"
    role_routes: dict[str, str] = Field(default_factory=lambda: {
        "planner": "strong",
        "synthesis": "strong",
        "judge": "strong",
        "classify": "cheap",
        "search": "cheap",
        "transform": "cheap",
        "heuristic-assist": "cheap",
        "default": "default",
        "strong": "strong",
        "cheap": "cheap",
    })
    prefer_cheap_for_simple: bool = False
```

#### Environment Variable Support (`RoutingConfig.from_env()`):
The configuration seamlessly hydrates from runtime environment variables without code modification:
- `YC_ROUTING_ENABLED`: Boolean toggle (`1`, `true`, `yes`).
- `YC_ROUTING_DEFAULT_PROVIDER`: Default provider name (default: `"default"`).
- `YC_ROUTING_STRONG_PROVIDER`: Strong model provider key (default: `"strong"`).
- `YC_ROUTING_CHEAP_PROVIDER`: Cheap model provider key (default: `"cheap"`).
- `YC_ROUTING_PREFER_CHEAP_FOR_SIMPLE`: Boolean flag to route simple, low-risk turns to cheap provider.
- `YC_ROUTING_ROLE_ROUTES`: JSON object or comma-separated pairs (`"planner:bedrock,classify:ollama"`).

---

### 2.3 Model Router (`ModelRouter`)

The `ModelRouter` manages provider registrations, evaluates task context, records OpenTelemetry attributes, and delegates completions:

#### Provider Resolution Cascade:
When resolving a provider for a requested role:
1. **Task-Adaptive Shift**: If a `TaskAnalysis` object is supplied and `prefer_cheap_for_simple=True`:
   - If `complexity == "simple"` and `risk == "low"`: Role is shifted to `ModelRole.CHEAP`.
   - If `complexity == "complex"` or `risk in ("high", "critical")`: Role is shifted to `ModelRole.STRONG`.
2. **Role Mapping Lookup**: Checks `config.role_routes` for target role (e.g. `planner -> strong`, `classify -> cheap`).
3. **Provider Instance Resolution**: Checks if the mapped provider name exists in registered providers.
4. **Alias Fallback**: If mapped name is `"strong"`, checks `config.strong_provider`; if `"cheap"`, checks `config.cheap_provider`.
5. **Deterministic Single-Provider Fallback**:
   - If only one provider is registered in `_providers`, it is returned unconditionally for all roles.
   - If `default_provider` was passed, returns `default_provider`.
   - If `"default"` or `"fake"` exists in `_providers`, returns it.
6. **Polymorphic Provider Interface**: `ModelRouter` inherits from `ModelProvider`. Calling `await router.complete(request)` delegates directly to the default routed provider.

---

## 3. Orchestration Subsystem Wiring

Model routing integrates cleanly into all orchestration subcomponents:

```
Incoming Turn
     │
     ▼
[TaskAnalyzer] ──────────► Uses ModelRole.CLASSIFY (Cheap / Local Provider)
     │
     ├─► Strategy: "direct" ────► If simple & low-risk: ModelRole.CHEAP
     │                           Else: ModelRole.DEFAULT
     │
     └─► Strategy: "plan_and_execute"
              │
              ├─► [Planner Node] ──────► Uses ModelRole.PLANNER (Strong Provider)
              ├─► [Execute Step Node] ─► Uses ModelRole.DEFAULT or Subagent
              │        │
              │        └─► (Subagent) ─► Uses spec.model_role via Router
              │        └─► (Retry) ────► Uses ModelRole.STRONG
              │
              └─► [Synthesizer Node] ──► Uses ModelRole.SYNTHESIS (Strong Provider)
                       │
                       ▼
               [Verifier Judge] ──────► Uses ModelRole.JUDGE (Strong Provider)
```

1. **TaskAnalyzer (`task_analyzer.py`)**:
   - Model-assisted task classification executes against `router.get_provider(ModelRole.CLASSIFY)`.
   - Prevents burning frontier model tokens on classification tasks.
2. **Verifier (`verifier.py`)**:
   - Model-judge evaluations execute against `router.get_provider(ModelRole.JUDGE)`.
   - High-precision rubric scoring is performed by the strong reasoning provider.
3. **Plan-and-Execute Workflow (`planner.py`)**:
   - Plan decomposition node executes against `router.get_provider(ModelRole.PLANNER)`.
   - Final response synthesis executes against `router.get_provider(ModelRole.SYNTHESIS)`.
   - Discrete plan steps execute against `router.get_provider(ModelRole.DEFAULT)`.
   - Failed step retry revisions execute against `router.get_provider(ModelRole.STRONG)`.
4. **Subagent Runner (`subagent.py`)**:
   - Child subagents dynamically resolve their provider via `router.get_provider(spec.model_role)`.
5. **Adaptive Orchestrator (`orchestrator.py`)**:
   - Manages router lifecycle and coordinates role dispatch across subcomponents.
   - For direct turns, evaluates `analysis.complexity` and `analysis.risk`. When `prefer_cheap_for_simple=True`, dispatches the direct turn loop to the cheap provider.
   - If output verification fails and triggers revision, dispatches revision to `ModelRole.STRONG`.
6. **Agent Integration (`agent.py`)**:
   - `AgentState` accepts an optional `active_provider: ModelProvider`.
   - `model_node` executes against `state.get("active_provider") or self.provider`.
   - `Agent.run()` and `Agent.handle()` pass routed providers through to direct turn executions.

---

## 4. OpenTelemetry Distributed Tracing

Routing decisions are recorded using GenAI-aligned semantic attributes:
| Attribute Name | Description | Example Values |
|---|---|---|
| `yusufclaw.routing.role` | Functional role for the invocation | `"planner"`, `"synthesis"`, `"judge"`, `"classify"`, `"default"` |
| `yusufclaw.routing.provider` | Resolved provider identifier | `"strong"`, `"cheap"`, `"bedrock"`, `"ollama"`, `"fake"` |
| `yusufclaw.routing.target` | Target model name or family | `"llama3.2"`, `"claude-3-5-sonnet"` |
| `yusufclaw.routing.reason` | Human-readable resolution rationale | `"Task complexity=simple and risk=low -> routed to cheap provider"` |

These attributes are recorded across all relevant spans:
- `yusufclaw.orchestration.task_analysis`
- `yusufclaw.orchestration.plan_generation`
- `yusufclaw.orchestration.synthesis`
- `yusufclaw.orchestration.verification`
- `yusufclaw.orchestration.turn`
- `yusufclaw.orchestration.subagent`

---

## 5. Usage Examples

### 5.1 Programmatic Setup with Multiple Providers

```python
from yusufclaw.models import FakeProvider, ModelRouter, RoutingConfig
from yusufclaw.orchestration import AdaptiveOrchestrator, OrchestrationConfig

router = ModelRouter(
    providers={
        "strong": FakeProvider(default_text="Strong response"),
        "cheap": FakeProvider(default_text="Cheap response"),
    },
    config=RoutingConfig(
        enabled=True,
        prefer_cheap_for_simple=True,
    ),
)

orchestrator = AdaptiveOrchestrator(
    router=router,
    config=OrchestrationConfig(enabled=True),
)
```

### 5.2 Agent Initialization with Router

```python
from yusufclaw.agent import Agent
from yusufclaw.models import get_model_router

router = get_model_router()
agent = Agent(
    router=router,
    enable_orchestration=True,
)
```

---

## 6. Verification and Quality Gates

The implementation adheres to YusufClaw's strict quality standards:
- **Test Suite**: 158 passed, 1 skipped (100% offline, ~21.0s).
  - 19 dedicated unit and integration tests in `tests/python/test_routing.py`.
  - 20 orchestration tests in `tests/python/test_orchestration.py` passed with zero regressions.
  - 119 tests across models, memory, security, gateway, skills, routines, and telemetry passed with zero regressions.
- **Linter**: `uv run ruff check .` reports `All checks passed!` (0 errors, 0 warnings).
- **Type Checker**: `uv run mypy src` reports `Success: no issues found in 69 source files` (Strict mode).
- **Architecture Integrity**: No modifications were made to `gateway/`, `security/`, `memory/`, `mcp/`, `sandbox/`, `skills/lifecycle.py`, `routines/`, `infra_py/`, or TypeScript `packages/`.
