# Durable Python job queue

The Python rewrite replaces the Redis list with SQL-backed jobs. PostgreSQL is the
production source of truth; SQLite is the local fallback. No coordination or memory
package is needed by the queue. The design follows ARCHITECTURE-v2 layers 2–3 and 8,
RESEARCH.md's durable inbox, fencing and approval-wait recommendations, and
RESEARCH-ASIST.md §2.2's bounded delegation and separate instruction/data channels.
The older architecture document's TypeScript decision is superseded for this work
by the Python rewrite scope.

## Configuration and execution

- `YUSUFCLAW_QUEUE_ENABLED=1` enables queued routines and delegation. Default is off
  outside tests; the Python test fixture enables it with an isolated database.
- `YUSUFCLAW_QUEUE_URL=postgresql+psycopg://user:password@host/database` selects
  PostgreSQL (`postgresql://` is normalized). Default:
  `sqlite+aiosqlite:///queue.sqlite`, relative to the working directory.
- `await queue.initialize()` creates the queue's four dedicated tables. This is the
  first schema version, not a migration framework. Provision the database first;
  subsequent schema changes will need explicit migrations. No Redis migration is
  attempted: drain any legacy Redis messages before enabling the new producer.
- Use one trusted handler registry per worker security context. Payloads are JSON,
  never pickled Python callables, credentials, or serialized provider instances.

```python
from yusufclaw.queue import JobQueue, Limit, Worker

queue = JobQueue("sqlite+aiosqlite:///queue.sqlite")
await queue.initialize()
await queue.set_limit("user", "local", Limit(concurrency=3, budget=100_000))
await queue.set_limit("role", "researcher", Limit(concurrency=2, budget=50_000))

async def research(job):
    await queue.progress(job.id, job.lease_token, 50)
    return {"summary": "completed"}

job = await queue.enqueue("research", {"topic": "example"}, user_id="local",
                          role="researcher", priority=10, budget=4000,
                          idempotency_key="request-123")
await Worker(queue, {"research": research}).run_once()
await queue.close()
```

`Worker.run(stop_event)` continuously polls; start multiple Worker instances for
parallel execution. Each runs one handler at a time. Handlers must yield to asyncio;
blocking CPU or synchronous I/O belongs in a thread/process. Cancellation of a
worker cancels its handler and leaves its lease for recovery. Shutdown is cooperative:
a handler that suppresses cancellation cannot be forcibly killed by asyncio.

Optional Redis hints: construct `RedisWakeup(url)`, pass `notify=wakeup.notify` to
`JobQueue` and `wakeup=wakeup` to `Worker`. Producers notify after the SQL commit.
Redis failures do not undo accepted jobs; workers continue bounded polling even if
hints are lost. Close the wakeup client separately.

## State and delivery contract

| From | Operation | To |
| --- | --- | --- |
| queued | claim after `run_at`, admission allowed | running |
| running | fenced finish | succeeded |
| running | error or expired lease, attempts remain | queued |
| running | error or expired lease, attempts exhausted | failed + dead_letter |
| running | handler raises `WaitingApproval` | waiting_approval |
| waiting_approval | trusted `resume` | queued |
| queued/running/waiting_approval | cancel | cancelled |

Terminal jobs never reopen. Denial uses cancellation. `resume` is an internal
trusted API, **not** an approval validator: the caller must authenticate the actor,
validate the action card's nonce/hash/expiry and recheck permissions before resuming.
Handlers restart from durable payload/result state, not a suspended Python stack.
Approval waiting releases the lease and concurrency slot. It does not refund usage.

Each claim receives a fresh random lease token. Finish, progress, and heartbeat
require that token, running state, and an unexpired lease. Workers heartbeat every
lease/3; defaults are 30-second leases and 0.5-second idle polling. Claim sweeps dead
leases, increments attempts only on actual execution, and schedules exponential
backoff (`backoff * 2**(attempts-1)`, exponent capped at 30). Default maximum is three
attempts. Failed jobs stay in the table with error and dead-letter metadata for
inspection; there is no automatic redrive or destructive retention job.

Priority is descending, then `run_at` and ID ascending. `run_at` is a UTC Unix
seconds timestamp. Idempotency is unique per `(user_id, idempotency_key)` for the
entire lifetime of the stored job, including terminal jobs. A duplicate returns the
original job; it does not replace its payload. Null keys are not deduplicated.

Delivery is **at least once**. Fencing stops stale database writes, not a remote
side effect already in flight. Handlers must use stable external operation IDs or
receipts for side effects. Cancellation prevents subsequent claims and fences old
workers; active tasks observe it at their next heartbeat, not instantaneously.

## Concurrency, budgets, and parent jobs

Claims use `FOR UPDATE SKIP LOCKED` on PostgreSQL. A short database admission-gate
transaction serializes cross-job accounting and mutation, including SQLite writers.
No database lock is held while executing a handler. This deliberately favors simple,
correct admission over high claim throughput: there is a single admission gate,
and policy checks currently scan job metadata. Large installations will need indexed
accounting tables, pagination, retention and finer-grained locks before scaling.

Per-user and per-role policies are persisted by `set_limit`; all workers enforce
both. Default concurrency is three for each user and role, with no aggregate budget
cap until configured. `concurrency_key` adds per-user single-flight behavior across
jobs (used for routines and orchestration roots).

`budget` is an application-defined, nonnegative **per-attempt reservation**. Claims
charge it permanently and atomically against both user and role lifetime caps;
retries are charged again. This conservatively retains charges for crashed attempts.
There is no calendar reset, automatic actual-cost refund, or currency conversion.
Use one unit consistently within a database. Subagent integration reserves
`max_tokens * max_steps` token units; its existing BudgetManager still checks actual
model usage and turn/session budgets. The queue's reservation alone is not a hard
provider billing cap. Zero-reservation jobs still obey concurrency limits. Trusted
producers/admins own budget assignment and policy updates.

A child references an existing active parent with the same user. Cancelling a parent
cancels all nonterminal descendants atomically. Claim also cancels descendants of
failed parents (including parents exhausted by lease reclaim). Completed child
results remain available. A running parent counts toward its user's concurrency
limit; configure at least two slots when it waits synchronously for a child. Otherwise
children remain queued until cancellation/parent timeout frees capacity.

## Routines and delegation

With the flag enabled, `RoutineScheduler.tick` only enqueues matching cron minutes.
The key includes routine identity, target, and UTC minute. The continuous `run`
method starts its worker alongside ticking. Standalone schedulers can register
`{"routine": scheduler.handle_job}` with an external Worker. Schedule jobs carry
the complete validated routine snapshot and `run_at`; failed transport calls reach
the queue retry policy. Existing channel authorization remains in ChannelRunner.
Successful delivery writes the existing memory completion marker. Missed cron
minutes are still skipped; already enqueued jobs survive downtime.

SubagentRunner.run enqueues and waits for a worker result, preserving its caller API.
The embedded worker claims only that job; `handle_job` is available for a recovery
worker configured with the same principal, provider, tools and grants. Requested
tools are intersected before serialization and again at execution. Instruction and
context remain separate fields. Failures are failed jobs with structured results.

AdaptiveOrchestrator enqueues an orchestration root for delegated plans and links
subagent jobs to it. `orchestrator.handle_job` can be registered for recovery with
the same trusted configuration and queue URL. Embedded workers preserve the existing
synchronous orchestration API while execution happens in a worker task. Orchestration
and subagent jobs currently use one attempt to avoid silently replaying whole plans;
routines/general jobs default to three. A crashed one-attempt plan becomes dead-letter
on reclaim. Plan-step checkpoint/resume and external side-effect ledgers remain
handler responsibilities.

## Progress, tracing, and inspection

State events and progress are committed in the same database as jobs.
`queue.events(job_id, after=cursor)` returns ordered AG-UI `CUSTOM` envelopes named
`job.queued`, `job.running`, `job.progress`, etc., with durable integer cursors.
Progress accepts only a percentage, so raw prompts/errors never enter these events.
Gateway consumers must authenticate and filter by job owner before exposing them;
this change does not modify web streaming or install an SSE route.

The enqueue trace carrier is persisted. Worker spans named
`yusufclaw.queue.execute` restore that context and record job ID, parent ID, kind,
attempt and exception type, without payloads. Existing model telemetry continues
inside those spans.

```sh
uv run python -m yusufclaw.queue list
uv run python -m yusufclaw.queue show JOB_ID
uv run python -m yusufclaw.queue events JOB_ID
uv run python -m yusufclaw.queue cancel JOB_ID
```

The CLI is a local operator interface using database access, not a public API.
`list` excludes payload/results; `show` includes them and therefore requires trusted
terminal access. Failed entries include `dead_letter`. Protect database files,
credentials, backups and results as application data. Hosts should use synchronized
UTC clocks for leases.

## Validation

`tests/python/test_queue.py` covers state transitions, invalid/stale writes,
retry/backoff/exhaustion, heartbeat/reclaim, cancellation and descendants, concurrent
idempotency, persistence across clients, scheduling/priority, user/role admission,
worker timeout/shutdown, approval wait, routine and parent/child integration, tracing,
and live Postgres skip-locked contention. Legacy routine tests explicitly disable
the flag to retain coverage of the fallback path.

```sh
uv run pytest -q
uv run ruff check .
uv run mypy src
# Optional dedicated local Postgres database; each fixture creates/drops its own schema:
QUEUE_TEST_POSTGRES_URL=postgresql://USER:PASSWORD@HOST/TEST_DB \
  uv run pytest -q tests/python/test_queue.py
```

The Postgres test account needs CREATE SCHEMA permission. Tests never deploy AWS.
