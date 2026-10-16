# YusufClaw design recommendations and roadmap

Date: **2026-10-16**. Input: [SPEC.md](SPEC.md). Evidence and tradeoffs: [RESEARCH.md](RESEARCH.md). This document proposes implementation work; none of the packages, tests, or infrastructure described below has been created by this research task.

Build a local, durable, single-owner agent with centralized permission checks, explicit memory, and a shared gateway. Ship CLI and Telegram with Bedrock/Ollama first. Add autonomous breadth only after restart behavior, approvals, and retrieval have measurable correctness.

**Priorities:** P0 = local Phase 1 release requirement; P1 = next increment; P2 = conditional expansion. Phases below are dependency-ordered, not calendar estimates. AWS deployment remains out of scope until a later deployment task.

## 1. Decisions to carry into implementation

| ID | Decision | Reason and evidence | Priority |
|---|---|---|---|
| ADR-01 | One gateway daemon; CLI/Telegram submit to the same core owner | OpenClaw shows central channel/control ownership; separate adapters should not fork agent state. [Architecture](https://github.com/openclaw/openclaw/blob/main/docs/concepts/architecture.md) | P0 |
| ADR-02 | Bounded ReAct-style loop with persisted transitions | Adapt actions to observations while making failures inspectable. [ReAct](https://arxiv.org/abs/2210.03629) | P0 |
| ADR-03 | SQLite canonical state; FTS first; embeddings optional | Keep local setup small and exact identifiers searchable. [FTS5](https://www.sqlite.org/fts5.html) | P0/P1 |
| ADR-04 | Profile, episodes, facts, and skills are separate memory structures | Combine tiered context with explicit knowledge updates. [MemGPT](https://arxiv.org/abs/2310.08560), [LongMemEval](https://arxiv.org/abs/2410.10813) | P0/P1 |
| ADR-05 | Policy checked outside the model for every tool and delegate | Untrusted tool data can redirect agent behavior. [AgentDojo](https://arxiv.org/abs/2406.13352) | P0 |
| ADR-06 | OS-isolated executors; fail closed when unavailable | Browser/tool middleware is not itself a security boundary. [Playwright MCP](https://github.com/microsoft/playwright-mcp) | P0 before execution |
| ADR-07 | Model providers return normalized events; tools stay client-controlled | Bedrock client-side tool calling allows our own policy gate. [Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use-client-side.html) | P0 |
| ADR-08 | Persistent routines and explicit plans before broad multi-agent orchestration | Plans/observations and reusable skills are separable capabilities. [ReWOO](https://arxiv.org/abs/2305.18323), [Voyager](https://arxiv.org/abs/2305.16291) | P1 |
| ADR-09 | Subagents are bounded tool jobs with narrower grants | Conversational orchestration is useful, but does not supply our security guarantees. [AutoGen](https://arxiv.org/abs/2308.08155) | P2 |
| ADR-10 | Propose Node 24 LTS in place of the spec's Node 20 | Node 20 is EOL. Record this as a deliberate spec amendment when implementing. [Node status](https://nodejs.org/en/download/archive/v20) | P0 decision |

Do not silently rewrite SPEC.md. If Node 20 remains a hard compatibility constraint, pin a tested dependency set and document the maintenance exception; it should not be the recommended baseline for a new always-on deployment.

## 2. Package ownership and dependency direction

Keep the five packages specified in SPEC.md; split more packages only when there is a concrete need.

| Package | Owns | Must not own |
|---|---|---|
| `gateway` | Adapter authentication, normalized input, session routing, UI/CLI controls, streaming, outbound delivery | Model policy decisions or direct shell execution |
| `core` | Run state machine, budgets, context assembly, persistence interfaces, memory lifecycle, approval service, scheduler, audit events | Provider credentials in prompts or channel-specific API assumptions |
| `models` | Bedrock/Ollama/compatible-provider adapters, schema translation, capability checks, usage/error normalization | Tool execution or automatic cloud failover against privacy settings |
| `tools` | File/fetch/shell/browser implementations, sandbox runtime, MCP client, later delegate adapters | Granting its own authority or changing policy configuration |
| `infra` | Later deployment definitions and cloud adapters' wiring | Phase 1 deployment or infrastructure required for local use |

`core` defines minimal provider/tool/store ports and accepts implementations by dependency injection. `models` and `tools` may import type-only ports; core must not import their concrete implementations. Gateway's composition root wires the system. Keep shared schemas in a narrow core export initially; move to a separate contracts package only if build boundaries justify it.

Recommended local process model: one Node daemon with SQLite and the Telegram polling adapter; CLI connects over a protected local socket or loopback endpoint. Tool execution is out of process. A supervisor can restart the daemon; startup scans incomplete runs and pending deliveries. Local operation still needs the machine awake and online for timely routines.

## 3. Contract sketch

These are proposed interfaces, not SDK APIs or drop-in implementation code. Runtime schema validation is required in addition to TypeScript types.

```ts
type RunStatus =
  | "queued" | "running" | "awaiting_approval" | "awaiting_input"
  | "succeeded" | "failed" | "cancelled" | "outcome_unknown";

interface MessageEnvelope {
  principalId: string;
  channel: "cli" | "telegram" | "web" | "whatsapp" | "email" | "voice";
  externalMessageId: string;
  sessionId: string;
  receivedAt: string;
  replyTargetId: string; // server-resolved, never selected by fetched content
  text: string;
  attachmentIds: string[];
}

interface ToolProposal {
  operationId: string;  // assigned by core, stable across transport retries
  runId: string;
  toolId: string;
  toolVersion: string;
  arguments: unknown;
}

interface ApprovalBinding {
  principalId: string;
  operationId: string;
  toolVersion: string;
  canonicalArgumentsHash: string;
  resourcePreconditionHash: string;
  policyVersion: string;
  expiresAt: string;
  nonce: string;
}

interface MemoryClaim {
  id: string;
  principalId: string;
  scopeId: string;
  kind: "semantic" | "episodic" | "procedural";
  text: string;
  sourceIds: string[];
  trust: "user_explicit" | "observed" | "model_inferred" | "external";
  observedAt: string;
  validFrom?: string;
  validTo?: string;
  supersedesId?: string;
  revision: number;
  status: "candidate" | "active" | "superseded" | "deleted";
}
```

Also define `ModelEvent` variants for text deltas, completed tool calls, usage, finish, and error; `ToolResult` variants for success/error/unknown outcome with artifact and receipt IDs; `RunEvent` with monotonic sequence and schema version. A completed tool call is emitted only after the adapter has assembled all arguments. Policy resolves resource access/effects from the validated arguments and trusted tool metadata, never from model-provided risk labels.

Separate `StateStore` (durable events/operations) from `MemoryIndex` (FTS/semantic lookup). Introduce `Clock`, `IdGenerator`, and `Executor` ports so tests can inject failures and time without real external side effects.

## 4. Persistence and recovery design

Initial SQLite tables: `inbox`, `sessions`, `runs`, `run_events`, `tool_operations`, `approvals`, `outbox`, `memory_records`, `memory_sources`, `memory_revisions`, `memory_deletions`, `jobs`, and `job_occurrences`. Add embedding/index metadata in P1. This table list is a design proposal; migration naming can follow project conventions.

Important constraints:

- Unique inbound key: `(channel, channelAccountId, externalMessageId)`.
- Unique run sequence: `(runId, sequence)`; optimistic version on the run and memory claims.
- Unique operation ID and one-time approval consume; nonce hash stored rather than reusable plaintext where practical.
- Unique routine occurrence: `(jobId, scheduledInstant, scheduleVersion)`.
- Outbound deduplication key binds run, destination, and message purpose; transport retries retain it.
- Scope and deletion predicates apply to every retrieval; FTS/vector indexes are rebuildable derivatives.

Transaction boundaries: accept input and enqueue work atomically; persist tool intent before dispatch; commit result and next run transition together; enqueue final delivery with run completion. Never hold a database transaction while waiting on a model, tool, or person. Preserve operational audit metadata while keeping deletable private payloads separately addressable.

Recovery matrix:

| Failure point | Recovery behavior |
|---|---|
| Before inbox commit | Provider may retry; normal deduplication applies |
| After acceptance, before run begins | Recover queued run on startup |
| During model stream | Discard incomplete proposal; retry within budget using committed context |
| Before tool dispatch, after intent recorded | Revalidate policy/approval and dispatch if safe |
| During/after side effect, no durable receipt | Mark `outcome_unknown`; reconcile through external ID/status, otherwise require intervention |
| While awaiting approval | Restore pending approval with original expiry and resource binding |
| After final state, before reply delivery | Retry outbox; do not rerun the task |
| During consolidation/deletion | Resume by watermark/version; deletion prevents stale worker writes |

Exactly-once external effects are not promised. Each tool declares whether it supports idempotency or reconciliation. Non-idempotent sends without receipts need an explicit unknown-outcome path.

## 5. Phase 1 — local working agent, P0

Implement in this order. All tasks are local; Bedrock requests use the existing AWS profile, not newly deployed infrastructure.

| Ticket | Owner | Concrete deliverable | Dependencies / acceptance gate |
|---|---|---|---|
| P0-01 | repository/core | pnpm workspace, strict TS, package exports, supported Node decision, config validation, README skeleton | Clean install/build/typecheck; no committed secrets; infra disabled |
| P0-02 | core | Schemas, run machine, SQLite migrations, inbox/outbox, event/operation ledger | P0-01; migration round-trip and queued-run restart test |
| P0-03 | models | Fake provider plus Bedrock and Ollama adapters | P0-01; shared contract suite covers fragmented arguments, errors, tool ID/result pairing, usage and cancellation |
| P0-04 | core/tools | Deterministic policy, approval service, resource checks, sandbox executor | P0-02; deny/approve/expiry/replay and fail-closed tests pass |
| P0-05 | tools | Scoped files, constrained web fetch, isolated shell | P0-04; path/symlink/SSRF/egress tests; time/output/process limits enforced |
| P0-06 | core | Bounded loop, per-session queue, cancellation, recovery | P0-02–05; verified tool task succeeds; malformed call never executes; injected crash does not blindly repeat effects |
| P0-07 | core | Transcript/episodes, explicit remember/revise/forget, small profile and FTS | P0-02; restart recall, supersession, scope isolation, and deletion tests |
| P0-08 | gateway | CLI and Telegram polling adapters, allowlist, status/approval/cancel commands | P0-06–07; duplicate updates produce one run; unauthorized users create none |
| P0-09 | all | Redacted traces, local evaluation fixtures, setup/recovery/backup instructions | P0-08; release suite and live provider smoke checks pass |

Phase 1 exit gate:

- CLI and Telegram can complete a bounded file/research task and cite its tool evidence.
- Bedrock uses profile `yusufclaw`, region `us-east-1`; Ollama can execute the same supported tool schema. Model IDs are configurable.
- Destructive/outbound actions wait for a concrete bound approval; denied or expired actions have no effect.
- Shell execution is isolated. If the sandbox is unavailable, the shell tool is disabled with an actionable error.
- Duplicate inputs, restarts, and unknown external outcomes are visibly and safely handled.
- Explicit user memory survives restart; forgetting removes it from active retrieval and derived context.
- At least 40 deterministic behavior/security scenarios pass; zero unauthorized effects and secret leakage in those fixtures. This is a test gate, not a general security guarantee.
- README covers prerequisites, credentials, allowlisting, running CLI/Telegram, stopping the daemon, budgets, retention, and recovery. No AWS deployment is needed.

## 6. Phase 2 — useful persistence and controlled automation, P1

| Ticket | Owner | Deliverable | Acceptance / dependency |
|---|---|---|---|
| P1-01 | models | OpenAI-compatible adapter | Same provider contract tests; no silent privacy-changing fallback |
| P1-02 | core | Hybrid retrieval, embedding versioning, evidence-linked extraction | Compare with FTS baseline on held-out Turkish/English cases; log ingestion + retrieval cost |
| P1-03 | core | Consolidation, correction UI/API, deletion propagation | Derived records trace to sources; competing updates use CAS; delayed jobs cannot resurrect deleted facts |
| P1-04 | gateway | Web chat, event cursor/resume, run history, approvals and memory viewer | Reconnect recovers status; private scopes stay isolated; escaped rendering and authenticated streams |
| P1-05 | core | Durable cron/reminders and bounded heartbeat | Restart/missed-run/DST/overlap tests; pending approval never becomes automatic execution |
| P1-06 | tools | Isolated Playwright browser | Successful browser fixtures plus injection/redirect/download tests; consequential clicks require approval |
| P1-07 | tools | MCP client and approved server registry | Pin SDK/protocol; validate descriptions/schema changes; credential scope tests; no policy bypass |
| P1-08 | core/tools | Reviewed skills library, small plan DAG, bounded reflection | A repeated task reuses a tested skill; failure replans within budget; inferred lesson remains reversible |
| P1-09 | core | Build-versus-adopt spike: current Letta and LangGraph JS | Run identical representative tasks; compare recovery complexity, provider fit, latency, and policy integration |
| P1-10 | all | Public benchmark subsets and local adversarial expansion | Versioned manifests, repeated runs, task success plus security/latency/cost reporting |

P1-02/03 must follow the basic memory contract; P1-05 needs operation recovery and outbound delivery; P1-06/07 need completed sandbox/permission gates. These are not independent features to bolt on before P0 security works.

For MCP, use the stable v2 client package line and the 2026-07-28 protocol where supported; retain a tested compatibility path only if a selected server requires it. Do not mix v1 code examples with v2 package APIs. [Official SDK](https://github.com/modelcontextprotocol/typescript-sdk).

For P1-09, current Letta development is in `letta-code`; do not evaluate only the historical MemGPT server. LangGraph's checkpointing does not remove the need for our operation ledger. Choose one authoritative runtime; reject a design with two competing memory/approval/run owners. [Letta](https://github.com/letta-ai/letta-code), [LangGraph persistence](https://docs.langchain.com/oss/javascript/langgraph/persistence).

## 7. Phase 3 — bounded delegation and additional channels, P2

| Ticket | Deliverable | Go/no-go condition |
|---|---|---|
| P2-01 | `delegate` adapter for identified `agy` executable and codex CLI | Versioned protocol probe; isolated workspace; child budget/capability subset; no inherited host secrets |
| P2-02 | At most two child jobs, depth one, parent verification | Demonstrated improvement on independent subtasks under equal total budget; conflict and cancellation tests |
| P2-03 | WhatsApp adapter | Choose Baileys versus an officially supported integration after account/policy verification; dedicated credentials; reconnect/deduplication tests |
| P2-04 | Email adapter and draft workflow | Sender authentication is not confused with user authorization; quoted text/injections cannot approve sending |
| P2-05 | Push-to-talk voice, later realtime interruption | Transcription confirmation for consequential actions; audio retention configured; cancellation semantics explicit |
| P2-06 | Temporal graph / Mem0 / A-MEM-inspired links | Material held-out gain on relationship/update queries after counting ingestion cost and operating complexity |
| P2-07 | Optional OpenHands remote coding backend | Better artifact/test outcomes than simpler CLI delegate; separate worker credentials and authority |

For memory service experiments, retain canonical export/import and source IDs. Mem0 is an optional memory layer; Graphiti is an OSS temporal graph framework distinct from Zep's managed service. A-MEM's system repository differs from its reproduction repository. [Mem0](https://github.com/mem0ai/mem0), [Graphiti](https://github.com/getzep/graphiti), [A-MEM](https://github.com/agiresearch/A-mem).

Do not import AutoGPT Platform code assuming MIT: `autogpt_platform` and Classic have different licenses. Workflow ideas can inform routines without adopting that platform. [License boundary](https://github.com/Significant-Gravitas/AutoGPT/blob/master/LICENSE).

## 8. Later cloud phase — design now, deploy only later

Keep local SQLite operational until migration has a tested need. Future AWS work should preserve the same run/tool/approval contracts:

- Telegram webhook ingress authenticates, deduplicates, persists/enqueues, and returns promptly.
- A worker owns the run; long browser/coding jobs use an appropriate isolated worker rather than depending on the webhook request lifetime.
- DynamoDB access patterns need explicit partition/sort keys, conditional updates for versions/leases, and durable operation records. Port `StateStore`; do not translate arbitrary SQL mechanically.
- Memory retrieval remains a separate `MemoryIndex` decision. Do not assume FTS/vector behavior automatically follows a move to DynamoDB.
- S3 stores bounded artifacts behind scoped references; secrets remain outside model context and worker logs.
- A migration exports IDs/revisions/provenance, verifies counts and checksums, pauses writes for a controlled cutover, and has a rollback path. Test deletion state across migration and restore.
- CDK infrastructure and deployment permission are part of that later task. This roadmap authorizes no deployment.

## 9. Evaluation and release gates

Version every evaluation manifest with model/provider, prompts, tool schemas, dataset split, memory/index version, seed where supported, retry budget, and environment. Run mocked deterministic tests in ordinary CI and paid model evaluations in a separately controlled job. Hold out cases from skill development and memory consolidation.

| Gate | Proposed acceptance criterion | Evidence collection |
|---|---|---|
| Permission correctness | All denial, replay, changed-argument, and expiry fixtures pass; zero unauthorized effects | Executor dispatch log plus external-state assertions |
| Recovery | No blind repeat of non-idempotent operations in fault-injection cases | Intent/receipt ledger and simulated remote receipts |
| Memory correctness | All explicit correction/deletion/scope fixtures pass | Index queries, built prompt inspection, answer evidence IDs |
| Agent utility | Establish P0 baseline; changes must not regress held-out completion without documented tradeoff | Task outcome artifacts and repeated runs |
| Retrieval improvement | Hybrid/consolidated memory beats chosen baseline on target categories at acceptable measured cost | Recall@k, supported-answer accuracy, stale-fact and abstention rates |
| UX | Status and cancellation available during tools/approvals; reconnect restores runs | Adapter tests and manual CLI/Telegram/web walkthrough |
| Operating cost | Every run/routine enforces a configured ceiling and records usage | Budget tests, tokens, model/tool latency, total cost per success |

Performance targets should be set after measuring the target machine/models. Do not turn an arbitrary latency threshold into a research-backed requirement. Report p50/p95 by stage: queue, context retrieval, model, tool, approval wait, delivery. Separate user waiting time from compute time.

Use BFCL-style tool cases first, LongMemEval/LoCoMo for memory, AgentDojo/InjecAgent for security, WebArena for browser work, and SWE-bench-style plus TypeScript repository tasks for delegates. Public benchmark success does not replace local recovery and permission tests. Full source mapping and limitations are in [RESEARCH.md](RESEARCH.md).

## 10. First implementation slice

The first concrete vertical slice should be: **CLI sends an authenticated request → core persists it → fake provider proposes a scoped file read → policy allows it → tool returns bounded text → core records evidence → CLI receives the answer**. Inject a restart and repeat the request to verify recovery/deduplication. Then swap in Bedrock/Ollama, add a write requiring approval, and connect Telegram to the same core.

This sequence produces a reviewable working system early while preserving the contracts needed for memory, routines, and later delegation.
