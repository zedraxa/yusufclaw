# YusufClaw Adaptive Orchestration System (PY-ORCH)

## 1. Executive Summary & Architectural Positioning

The `yusufclaw.orchestration` package implements the Layer 2 Meta-orchestrator and Layer 8 Verification specifications defined in `docs/ARCHITECTURE-v2.md`, synthesizing research from `docs/RESEARCH.md` (Plan-and-Execute, ReWOO, Reflexion) and `docs/RESEARCH-ASIST.md` (AOrchestra, CaMeL channel separation, depth-1 subagent boundaries).

The orchestration subsystem provides an adaptive, budget-governed, verifiable execution pipeline that dynamically routes incoming requests to the optimal strategy:
1. **Direct Path**: Single-agent tool loop for straightforward requests.
2. **Plan-and-Execute Path**: LangGraph state machine decomposing complex workflows into verified steps.
3. **Subagent Delegation Path**: Focused, depth-1 specialist workers operating under strict least-privilege tool intersections and inherited budget constraints.

Integration into `src/yusufclaw/agent.py` is minimally intrusive and gated behind the `enable_orchestration: bool = False` flag, ensuring 100% backwards compatibility with existing turn loops.

---

## 2. Core Components

### 2.1 Task Analyzer (`src/yusufclaw/orchestration/task_analyzer.py`)

The Task Analyzer evaluates each incoming prompt to determine its execution profile:
- **Complexity**: `simple`, `medium`, `complex` (evaluated from character volume, step conjunctions, tool count, and multi-step numbered lists).
- **Required Tools**: Pattern-matched candidate tool families (`shell`, `file_read`, `file_write`, `file_list`, `web_fetch`, `memory`).
- **Risk Level**:
  - `critical`: Destruction commands (`rm -rf`, `drop table`, `mkfs`, `format`, `curl | bash`, `eval(`).
  - `high`: Modification of sensitive assets (`delete`, `chmod`, `sudo`, `token`, `password`, `secret`, `private_key`).
  - `medium`: State-changing operations (`write`, `edit`, `post`, `upload`, `curl`, `fetch`).
  - `low`: Read-only queries, conversations, explanations.
- **Strategy Selection**:
  - `direct`: Single-step queries or low-complexity tasks.
  - `plan_and_execute`: Multi-step sequential tasks requiring intermediate verification.
  - `delegate`: Complex tasks with distinct domains or explicit delegation triggers (`subagent`, `in parallel`, `delegate`).

#### Modes of Operation:
1. **Deterministic Heuristic Engine**: Zero LLM calls, deterministic regex and keyword parsing, instant execution.
2. **Model-Assisted Mode**: Invokes `provider.complete()` with a strict JSON system prompt. Automatically falls back to the deterministic heuristic if the model output is malformed or times out.
3. **Distributed Tracing**: Records OpenTelemetry span `yusufclaw.orchestration.task_analysis` with complexity, risk, strategy, and step attributes.

---

### 2.2 Budget Manager (`src/yusufclaw/orchestration/budget.py`)

The Budget Manager monitors resource consumption against configurable ceilings across two independent scopes:
1. **Per-Turn Scope**: Budget allocated to a single prompt execution.
2. **Per-Session Scope**: Cumulative budget spanning multiple turns.

#### Monitored Dimensions:
- **Tokens**: Prompt tokens, completion tokens, total tokens.
- **Cost**: Dollar cost in USD calculated against configurable token pricing (`TokenPricing(prompt_cost_per_1m=..., completion_cost_per_1m=...)`).
- **Steps**: Iterations of the model/tool execution loop and subagent invocations.
- **Time**: Elapsed wall-clock execution seconds using monotonic timers.

#### Graceful Stop:
When any configured limit is exceeded, the orchestrator halts further tool execution and returns a structured degradation notice (`get_graceful_stop_message()`) preserving partial findings and session state rather than raising an unhandled failure.

---

### 2.3 Verifier (`src/yusufclaw/orchestration/verifier.py`)

The Verifier validates candidate turn outputs against defined `AcceptanceCriteria`:

#### Rule-Based Criteria:
- `min_length` / `max_length`: Character length boundaries.
- `required_keywords`: Mandatory terms that must appear (case-insensitive).
- `forbidden_keywords`: Terms that invalidate the response (e.g. error traces, leaked tokens).
- `required_pattern`: Regex pattern matching formatting requirements (e.g. JSON, markdown headers).
- `must_not_contain_patterns`: Regex patterns that disqualify the response.
- `expected_tools`: Verifies that mandatory tools were actually invoked during the turn.

#### Optional Model Judge:
When `model_judge_required=True`, the verifier prompts the model with a structured evaluation rubric and scores the candidate output.

#### Automated 1-Turn Retry:
If verification fails and remaining retries exist (`max_retries=1` by default), the verifier generates a targeted feedback prompt detailing the exact deficiencies. The orchestrator issues exactly one revision turn, improving output reliability.

---

### 2.4 Plan-and-Execute & Subagents (`src/yusufclaw/orchestration/planner.py`, `subagent.py`)

#### Subagent Isolation (`SubagentRunner`):
Following the architectural guidelines from `docs/RESEARCH-ASIST.md`:
- **Max Depth 1**: Subagents are strictly forbidden from spawning child subagents (`spec.max_depth` is clamped to 1).
- **Least-Privilege Tool Intersection**: Subagents only receive tools present in the parent agent's grants and explicitly allowed for the subtask.
- **Channel Separation**: The user's instruction is delivered via the trusted prompt channel, whereas context data is labeled under an untrusted data block, preventing subagent prompt injection escalation.
- **Budget Inheritance**: All tokens, steps, and time used by subagents are billed against the parent `BudgetManager`.

#### Deep Agents Integration:
The official `deepagents` package (LangChain Deep Agents, v0.7.26) was successfully installed via `uv add deepagents`. The architecture provides dual support:
1. `deepagents` integration harness when available and configured.
2. Native LangGraph `StateGraph` worker engine guaranteeing complete offline determinism with `FakeProvider` and zero external network calls in CI/tests.

#### Plan-and-Execute Workflow (`PlanAndExecuteWorkflow`):
A compiled LangGraph state machine:
```
[START] --> [planner] --> [execute_step] <--> [execute_step (more steps)]
                                 |
                          (all done / budget exceeded)
                                 v
                          [synthesizer] --> [END]
```
- **Planner Node**: Breaks down goal into `PlanStep` items.
- **Execute Step Node**: Dispatches steps either directly or to specialist `SubagentRunner` workers.
- **Step Verifier**: Verifies step output and triggers step retry if needed.
- **Synthesizer Node**: Combines step findings into a final cohesive response.

---

### 2.5 Adaptive Orchestrator (`src/yusufclaw/orchestration/orchestrator.py`)

The `AdaptiveOrchestrator` unifies the four subsystems:
```
Incoming Turn
     │
     ▼
[Budget Check: Turn Start] ────(Exceeded)────► [Graceful Stop Response]
     │ (OK)
     ▼
[Task Analyzer] (Complexity, Risk, Strategy)
     │
     ├─► Strategy: "direct" ────────────► [Agent Direct Execution Loop]
     │
     └─► Strategy: "plan_and_execute" ──► [LangGraph Plan & Execute Workflow]
                                                │
                                                ▼
[Output Verification] ◄─────────────────────────┘
     │
     ├─► Passed ────────────────────────┐
     │                                  │
     └─► Failed (1 Retry Recommended)   ▼
              │                  [Budget Check: Turn End]
              ▼                         │
     [1-Turn Retry Loop]                ▼
              │                  Return Final Output
              ▼
     [Re-verification] ─────────────────┘
```

---

## 3. Agent Integration

Integration in `src/yusufclaw/agent.py`:
```python
# Default initialization preserves original behavior:
agent = Agent(provider=provider)
assert not agent.enable_orchestration

# Enabling adaptive orchestration:
agent = Agent(
    provider=provider,
    enable_orchestration=True,
    orchestration_config=OrchestrationConfig(
        model_assisted_analysis=False,
        auto_retry_verification=True,
        budget_limits=BudgetLimits(max_tokens_per_turn=4096),
    ),
)
```

When `enable_orchestration=False`, the execution path through `Agent.run()` and `Agent.handle()` remains identical to prior releases.

---

## 4. OpenTelemetry Tracing

Orchestration emits standard OpenTelemetry spans:
| Span Name | Key Attributes |
|---|---|
| `yusufclaw.orchestration.turn` | `session_id`, `strategy_used`, `verified` |
| `yusufclaw.orchestration.task_analysis` | `complexity`, `risk`, `strategy`, `plan_required`, `delegate_required` |
| `yusufclaw.orchestration.budget_check` | `is_exceeded`, `turn_tokens`, `turn_cost`, `turn_steps` |
| `yusufclaw.orchestration.verification` | `passed`, `score`, `retry_recommended`, `retries_attempted` |
| `yusufclaw.orchestration.subagent` | `name`, `max_steps`, `tools_requested`, `success`, `tokens_used` |
| `yusufclaw.orchestration.plan_workflow` | `goal` |
| `yusufclaw.orchestration.plan_generation`| `steps_count` |
| `yusufclaw.orchestration.plan_step` | `step.id`, `step.description` |
| `yusufclaw.orchestration.synthesis` | `steps_used`, `budget_exceeded` |

All spans are verifiable in-memory without network connectivity via `InMemorySpanExporter`.

---

## 5. Verification and Quality Gates

The implementation adheres to the project's strict quality standards:
- **Test Suite**: 124 tests pass offline (100% pass rate in ~10.4s).
  - 20 comprehensive orchestration tests in `tests/python/test_orchestration.py`.
  - 104 existing tests across models, memory, security, gateway, skills, routines, and telemetry pass with zero regressions.
- **Linter**: `uv run ruff check .` reports `All checks passed!` (0 errors, 0 warnings).
- **Type Checker**: `uv run mypy src` reports `Success: no issues found in 57 source files` (Strict mode).
- **Isolation**: No modifications were made to `gateway/`, `security/`, `memory/`, `skills/`, `routines/`, `tools/`, `models/` internals, `infra_py/`, or TypeScript `packages/`.
