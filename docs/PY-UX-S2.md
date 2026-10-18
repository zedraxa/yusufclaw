# Python Web Gateway UX Sprint 2 — Memory Canvas (UX-07)

This document describes the UX-07 deliverable completing Sprint 2 trust/governance UI after UX-05/UX-06 landed in Sprint 1 merge.

---

## 1. Goals

Give the owner a transparent, editable view of durable memory for the active web session scope, with deterministic unlearning:

1. **Inspection canvas** — split pane: Profile & durable facts (`profile` + `fact`) vs episodic journal (`note`).
2. **Evidence & trust** — each row shows evidence ID (`mem_…`), trust pill, kind, and validity range.
3. **Edit** — in-place revise with optimistic concurrency (`expectedRevision` CAS).
4. **Forget** — confirmation modal → tombstone + FTS purge + removal from active retrieval.

Session scope matches chat isolation: `web:{userId}:{agentId}:{conversationId}`.

---

## 2. API

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/memory?agentId=&conversationId=&limit=` | Canvas payload: `profile`, `facts`, `journal` |
| `POST` | `/api/memory/forget` | Body: `recordId`, `agentId`, `conversationId`, optional `reason` |
| `PATCH` | `/api/memory/{record_id}` | Body: `text`, scope fields, optional `expectedRevision` |

Auth: same Bearer token as the rest of the gateway. Embeddings are never returned to the client.

Event-shaped response fields (for AG-UI alignment without requiring a live SSE subscription):

- `memory.canvas` on list
- `memory.record_deleted` on forget
- `memory.record_updated` on revise

---

## 3. UI

- Chat / Memory view tabs in the workspace header.
- Memory toolbar shows the resolved scope string and Refresh.
- Forget modal confirms unlearning; rows animate out (`vaporize`) on success.
- CSP unchanged: no `innerHTML`, no `localStorage` / `sessionStorage`; all DOM via `textContent`.

---

## 4. Backend wiring

- Reuses `SqliteMemory` / `PostgresMemory` via `MemoryStore` + `resolve()`.
- `forget()` already writes `memory_deletions` tombstones and deletes FTS rows.
- `revise()` preserves revision history and enforces CAS conflicts as HTTP 409.

---

## 5. Verification

```bash
uv run pytest -q tests/python/test_ux_sprint2_memory.py
uv run pytest -q
uv run ruff check .
uv run mypy src
```

Offline suite covers canvas listing/isolation, forget + FTS purge, revise CAS, and UI string presence.
