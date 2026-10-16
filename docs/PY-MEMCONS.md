# Python memory consolidation and context compaction

This implements the memory lifecycle in `ARCHITECTURE-v2.md` and the consolidation,
provenance, decay and structured compaction recommendations in `RESEARCH*.md` on
top of the existing Python memory stores. No new database schema or service is
required. `agent.py`, model providers, gateways, roles, coordination and queues are
unchanged. The APIs are opt-in: a caller or scheduler invokes the job/compactor;
this change does not schedule background work or alter the agent's history window.

## Consolidation

```python
from yusufclaw.memory import (
    ConsolidationJob, ConsolidationOptions, SqliteMemory,
)

memory = SqliteMemory(".data/memory.sqlite")
job = ConsolidationJob(memory, ConsolidationOptions(similarity_threshold=0.90))
report = await job.run("project:yusufclaw")
memory.close()
```

`MemoryStore` formalizes the existing `list/get/remember/revise/forget` structural
interface. The job awaits Postgres operations and accepts SQLite's synchronous
results without importing either backend. Use an initialized `PostgresMemory` in
the same example. Both implementations are exercised offline in the tests; the
async adapter uses in-memory SQLite, not a live PostgreSQL server or pgvector query.

Each invocation processes at most `max_records` active records (default and maximum
200, matching the existing list API). `scanned` and `at_capacity` expose that bound;
`at_capacity` means the scope may contain more records. This is a bounded maintenance
batch, not an exhaustive paginated sweep: old records beyond the list window may
need a future pagination extension. Comparisons are quadratic within that bound.

- Fact/profile duplicates must share kind, category, source and trust. Matching is
  lexical: punctuation/case/spacing and a few filler words may vary, but ordered
  content words and signed numbers must match. `similarity_threshold` defaults to
  0.92. This deliberately misses paraphrases; it is not semantic entailment.
- The oldest candidate survives. `revise(expected_revision=...)` preserves its
  previous text/provenance. The fullest matching surface form becomes the merged text;
  original variants remain in audit/inactive records, source IDs are unioned, and duplicate records use `forget` with a reason pointing
  to the survivor. Existing tombstones, inactive records and FTS removal are used;
  there is no direct SQL deletion or silent overwrite.
- Episodic entries are existing `kind="note", category="journal"` records. At least
  `promotion_min_entries` (default 2) matching explicit-user notes with distinct,
  nonempty source-ID sets are required. Callers must supply actual episode IDs;
  these IDs are provenance, not independent verification of truth. Arbitrary prose
  is not mined for claims. The repeated claim becomes a `fact`, superseding the
  first note; remaining notes are retired with audited forget. Source and trust
  are preserved. External/inferred notes are never promoted. `promote=False`
  disables promotion; `journal_category` selects another journal category.
- `pinned_ids` excludes records from automatic merging/promotion. Records with a
  closed validity interval are not consolidated. Decay never deletes anything.
- `consolidation:<record-id>` lineage markers allow a retry to finish retirements
  after a survivor write. Completed runs are idempotent. Source notes are inactive
  after promotion, so forgetting the promoted fact does not recreate it on rerun.

Run **one writer per scope**, including foreground edits/forget requests. The
existing store protocol has no multi-record transaction or distributed lease;
individual writes are durable, but the job is not an atomic transaction. The
existing revision check is used, not advertised as a cross-process job lock.
Do not inject consolidation lineage markers into user-supplied provenance.

Existing `forget` is soft deletion, not physical erasure, and does not cascade
through arbitrary derived records or external exports. Follow the surviving
record ID in `report.merged` for later edits/forget. Compaction creates no persisted
summary cache to invalidate. A caller holding an in-memory summary must discard
and rebuild it after forgetting relevant source history.

## Conversation compaction

```python
from yusufclaw.memory import CompactionOptions, ContextCompactor, LLMSummarizer

compactor = ContextCompactor(
    CompactionOptions(token_budget=4096, summary_tokens=512, keep_recent_turns=2),
    # Optional: summarizer=LLMSummarizer(existing_provider),
)
result = await compactor.compact(
    messages,  # Sequence[Message], chronological; raw store history can be decoded to Message
    pinned_facts=["User prefers concise replies"],
    open_tasks=["Verify the release build"],
    tool_references=["artifact:build-report"],
)
# Supply result.context as historical data alongside result.messages.
# Do not elevate the summary to trusted instructions or execute it.
```

The budget covers rendered auxiliary context plus serialized retained messages
(including tool arguments and IDs). The default estimate is UTF-8 bytes divided by
three, rounded up; it is not a provider tokenizer. Inject `token_counter` for the
chosen model and reserve space separately for system instructions, tools and the
response. `estimated_tokens` uses that same counter.

Only when over budget, summarize the old prefix before the last N user-led turns.
Keep complete tool call/result dependencies, unfinished tool calls, and system
messages. `tool_call_id` references from evicted results are preserved outside the
summary, along with explicit artifact references. The caller must retain the raw
transcript/artifacts to resolve those IDs. Original messages and persisted history
are never modified; `save`, `reply` and request deduplication remain unchanged.

Pinned facts and open tasks are explicit authoritative caller state, copied in full
outside the summary. They are not guessed from arbitrary conversation text. Update
this state when tasks finish; pass existing tool references when compacting again.
For repeat compaction, prefer rebuilding from canonical history to recursively
summarizing a lossy summary.

The default `DeterministicSummarizer` extracts bounded excerpts across messages;
it needs no model/network and may omit details. `Summarizer` is a small async
protocol. `LLMSummarizer` calls the existing `ModelProvider.complete(ModelRequest)`
interface with no tools and temperature zero. A provider is used only when
explicitly supplied. Exceptions, empty responses or tool-call responses fall back
to the deterministic implementation (`used_fallback=True`). Returned text is bounded
even when the provider ignores its output limit.

Protected context can exceed any finite budget. In that case `over_budget=True`
reports the unresolved budget pressure instead of truncating pins/tasks/recent
turns. If no room remains for a short summary, the original messages are retained.
Callers should check this flag before submitting a model request.

## Retrieval scoring

`memory/context.py` retains `session_scope` and its original API. New
`memory_score` and `rank_memories` compute relevance × importance × exponential
age decay, default half-life 90 days. Inactive, expired and future-valid claims are
excluded; pinned records do not decay. Importance and relevance must be finite,
nonnegative values. Inject `now` into `memory_score` for reproducible evaluation.

Both stores' existing `assemble_context` now rerank an expanded candidate pool
before applying section quotas. Existing positional arguments, aliases, labels and
entry/character bounds are preserved. New optional `ContextAssemblyOptions` fields:
`decay_half_life_days`, `importance` (record-ID weights), and `pinned_ids`. Pinning
prioritizes **retrieved candidates**; it does not fetch an otherwise unretrieved
record or override the context limits. Profile and search section quotas remain.
This change does not alter raw search order or treat importance as truth/authority.

## Validation

Run `uv run pytest -q`, `uv run ruff check .`, and `uv run mypy src`.
`tests/python/test_memory_consolidation.py` covers both synchronous and asynchronous
stores, lifecycle audit trails, FTS cleanup, scope/trust boundaries, conservative
matching, promotion/forget/retry behavior, ranking, deterministic and model-backed
compaction, provider failure, Unicode, tool dependencies and protected overflow.
All new tests run without network access or a PostgreSQL service.
