# YusufClaw Python Memory Subsystem (`py-memory`)

## 1. Overview & Objectives

This document summarizes the port of the durable structured memory subsystem from TypeScript (`packages/core/src/memory.ts`, `memory-tools.ts`, `session-context.ts`, and `tests/memory.test.ts`) into Python 3.12 under `src/yusufclaw/memory/`, based on [`docs/ARCHITECTURE-v2.md`](file:///home/yusuf/projects/wt/yc-pymem/docs/ARCHITECTURE-v2.md) and [`docs/RESEARCH.md`](file:///home/yusuf/projects/wt/yc-pymem/docs/RESEARCH.md).

All work strictly adheres to the project constraints:
- `packages/` (TypeScript monorepo) remains untouched.
- `src/yusufclaw/security/` and `src/yusufclaw/tools/` subpackages were untouched.
- No cloud or AWS resources were modified; all tests run completely offline without Docker.
- Full type-safety with `mypy` strict mode and lint compliance with `ruff`.

---

## 2. Architecture & Implementation

### 2.1 Pydantic Data Models (`src/yusufclaw/memory/models.py`)
- Strongly typed Pydantic v2 models representing the memory data structures:
  - `MemoryRecord`: Canonical durable memory item (`id`, `scope`, `kind`, `text`, `category`, `source`, `source_ids`, `trust`, `created_at`, `updated_at`, `valid_from`, `valid_to`, `supersedes_id`, `superseded_by_id`, `revision`, `status`, `deleted_at`, `embedding`).
  - `RememberInput`: Input payload for creating or superseding a memory record.
  - `ReviseInput`: Input payload for editing with optimistic concurrency control (`expected_revision`).
  - `SearchOptions` & `ListOptions`: Filter and pagination options.
  - `ContextAssemblyOptions`: Context bounding limits (`max_entries`, `max_chars`, `max_profile_entries`, `max_search_entries`).
  - `MemoryRevisionRecord` & `MemoryDeletionRecord`: Audit trails for historical versions and tombstones.
- Dual casing support via Pydantic `AliasChoices` and property aliases allows seamless usage with both Pythonic `snake_case` and TypeScript `camelCase` attributes (e.g. `source_ids` and `sourceIds`, `supersedes_id` and `supersedesId`).

### 2.2 Session Context Isolation (`src/yusufclaw/memory/context.py`)
- Ported TypeScript's `AsyncLocalStorage` to Python's standard `contextvars.ContextVar`.
- `active_session_context`: Thread-safe and coroutine-safe tracking of active `session` and `request_id`.
- `session_scope(session, request_id)`: Safe context manager for isolating execution scopes and preventing concurrent race conditions.

### 2.3 Full-Text Search Sanitization (`src/yusufclaw/memory/fts.py`)
- `sanitize_fts5_query`: Unicode-compliant regex tokenizer (`[\w]+` with `re.UNICODE`) matching TypeScript's `[\p{L}\p{N}_]+/gu`.
- Correctly handles international and Turkish characters (`İstanbul`, `Kadıköyde`, `Türkçe`, `çay`), builds prefix queries (`"token"*`), and strips special symbols (`:`, `?`, `*`, `(`, `)`) to prevent FTS5 syntax errors.
- `sanitize_tsquery`: PostgreSQL `to_tsquery` utility for boolean term matching.

### 2.4 SQLite FTS5 Storage Engine (`src/yusufclaw/memory/sqlite.py`)
- Zero-dependency local storage using Python's standard `sqlite3` with WAL mode, busy timeout, and foreign keys.
- **Relational Tables**:
  - `turns`: Turn history with `(session, request_id)` unique constraint for replay deduplication.
  - `state`: Key-value configuration/state store.
  - `memory_records`: Structured memory storage with kind checks (`profile`, `fact`, `note`) and status checks (`active`, `superseded`, `deleted`).
  - `memory_revisions`: Revision history recording superseded text, timestamps, and update reasons.
  - `memory_deletions`: Tombstone audit log for GDPR/right-to-forget compliance.
  - `memory_fts`: FTS5 virtual table for lexical BM25 ranked retrieval.
- **Key Operations**:
  - `remember`: Stores new record with canonical `mem_<base36>_<hex>` evidence ID. If `supersedes_id` is supplied, archives the old record and increments revision.
  - `revise`: Modifies record text, increments revision, and records historical state. Validates `expected_revision` for optimistic concurrency (CAS).
  - `supersede`: Convenience method replacing an older record.
  - `forget`: Soft-deletes record, removes from FTS5 index, and records tombstone.
  - `search`: Lexical FTS5 BM25 ranked retrieval with recency fallback.
  - `assemble_context`: Formats labeled markdown sections (`## Active Memory Context`, `### Profile`, `### Relevant Facts & Notes`) bounded by entry and character limits.

### 2.5 PostgreSQL & pgvector Async Engine (`src/yusufclaw/memory/postgres.py`)
- Asynchronous database engine built on SQLAlchemy 2.0 and `psycopg` async.
- Declarative models: `TurnModel`, `StateModel`, `MemoryRecordModel`, `MemoryRevisionModel`, `MemoryDeletionModel`.
- Native PostgreSQL `Vector(1536)` column via `pgvector.sqlalchemy`.
- Native PostgreSQL full-text search (`func.to_tsvector` and `func.plainto_tsquery` with `ts_rank`).
- `search_vector`: Semantic vector similarity search via pgvector cosine distance (`<=>`), with an in-memory cosine similarity fallback allowing tests to run completely offline without Docker.

### 2.6 Agent Memory Tools (`src/yusufclaw/memory/tools.py`)
- Exposes 4 callable agent tools subclassing `ToolDefinition`:
  1. `remember`: Stores durable facts, preferences, or notes into memory.
  2. `revise`: Updates existing facts with revision tracking.
  3. `forget`: Soft-deletes memory records with tombstone audit.
  4. `recall`: Queries memory via keyword / FTS5 search scoped to the active session.
- Seamlessly resolves scope from tool input, `active_session_context`, or fallback callback.

### 2.7 LangGraph Agent Integration (`src/yusufclaw/agent.py`)
- Updated `Agent` to natively support optional memory stores (`SqliteMemory` or `PostgresMemory`).
- `model_node`: Asynchronously calls `memory.assemble_context(session_id, user_text)` and injects labeled memory context into the system prompt.
- `tool_node`: Automatically dispatches memory tool calls (`remember`, `revise`, `forget`, `recall`) to the memory store.
- `Agent.handle(session, request_id, text)`:
  - Manages `session_scope(session, request_id)`.
  - Enforces text length constraints (1–16,000 characters).
  - Intercepts local commands (`/reset`, `/help`).
  - Performs request ID deduplication via `memory.reply(session, request_id)`.
  - Persists conversational turns via `memory.save(session, request_id, turn, reply)`.

---

## 3. Test Suite & Verification (`tests/python/test_memory.py`)

Every single TypeScript test case from `tests/memory.test.ts` was ported 1:1 to pytest:

| Test Case | Description | Status |
| :--- | :--- | :---: |
| `test_restart_recall` | Persists profile facts, notes, and user facts across database reopen; FTS5 search and context assembly after restart | Passed |
| `test_supersession` | Supersedes older facts, increments revision, and excludes older fact from active retrieval | Passed |
| `test_scope_isolation` | Strictly isolates memories between sessions and channels; blocks cross-scope mutations | Passed |
| `test_deletion` | `forget` removes memory from active retrieval and search; preserves audit tombstone | Passed |
| `test_fts_search_basics` | Prefix search, BM25 ranking, Turkish characters (`İstanbul`, `kahve`), category filtering, special symbol resilience | Passed |
| `test_optimistic_concurrency_revisions` | Revises memory with CAS check on `expected_revision`; logs revision history | Passed |
| `test_context_assembly_bounded` | Bounded entries and total characters, labeling sections with evidence IDs | Passed |
| `test_agent_memory_wiring` | LangGraph agent loop wires context assembly into prompt and executes memory tools | Passed |
| `test_sqlite_turn_history_and_reset` | Preserves turn history and `/reset` behavior while keeping durable memory intact | Passed |
| `test_create_memory_tools` | Directly tests schema validation and execution of all 4 memory tools | Passed |
| `test_postgres_memory_async` | Async SQLAlchemy operations (remember, revise, CAS conflict, FTS, history, state, tombstone) | Passed |
| `test_postgres_memory_vector_search` | Vector search using 1536-dimensional embeddings and cosine similarity | Passed |
| `test_pydantic_models_validation` | Pydantic v2 validation and dual camelCase/snake_case property access | Passed |

### Test & Linter Execution
```bash
# Run all Python tests (36 tests total)
uv run pytest

# Check code formatting & linting
uv run ruff check

# Strict static type check
uv run mypy src tests/python
```

**Results**:
- `uv run pytest`: 36 passed in 4.86s (100% pass rate, offline).
- `uv run ruff check`: All checks passed with 0 warnings.
- `uv run mypy src tests/python`: Success: no issues found in 33 source files.
