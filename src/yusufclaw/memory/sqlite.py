"""SQLite FTS5 backed durable structured memory implementation."""

import builtins
import json
import os
import secrets
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any

from yusufclaw.memory.fts import sanitize_fts5_query
from yusufclaw.memory.models import (
    ContextAssemblyOptions,
    ListOptions,
    MemoryRecord,
    RememberInput,
    ReviseInput,
    SearchOptions,
)


def _base36_encode(number: int) -> str:
    """Encode an integer into a lowercase base36 string."""
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    if number == 0:
        return "0"
    res: builtins.list[str] = []
    while number > 0:
        number, r = divmod(number, 36)
        res.append(chars[r])
    return "".join(reversed(res))


def generate_memory_id() -> str:
    """Generate a unique evidence ID in the canonical mem_<base36>_<hex> format."""
    timestamp_b36 = _base36_encode(int(time.time() * 1000))
    random_hex = secrets.token_hex(4)
    return f"mem_{timestamp_b36}_{random_hex}"


def _row_to_memory_record(row: sqlite3.Row) -> MemoryRecord:
    """Convert an SQLite row to a strongly typed MemoryRecord."""
    raw_source_ids = row["source_ids"]
    source_ids: builtins.list[str] = []
    if raw_source_ids:
        try:
            source_ids = json.loads(raw_source_ids)
            if not isinstance(source_ids, list):
                source_ids = []
        except Exception:
            source_ids = []

    return MemoryRecord(
        id=row["id"],
        scope=row["scope"],
        kind=row["kind"],
        text=row["text"],
        category=row["category"],
        source=row["source"],
        source_ids=source_ids,
        trust=row["trust"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        supersedes_id=row["supersedes_id"],
        superseded_by_id=row["superseded_by_id"],
        revision=int(row["revision"]),
        status=row["status"],
        deleted_at=row["deleted_at"],
    )


class SqliteMemory:
    """SQLite-backed structured memory with FTS5 BM25 search and session turn history."""

    def __init__(self, path: str = ":memory:", options: Any | None = None) -> None:
        self.path = path
        self.options = options

        if path != ":memory:":
            dirname = os.path.dirname(os.path.abspath(path))
            os.makedirs(dirname, mode=0o700, exist_ok=True)

        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        if path != ":memory:":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass

        with self._conn:
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._conn.execute("PRAGMA foreign_keys = ON")

            # Turn history and state
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    messages TEXT NOT NULL,
                    reply TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(session, request_id)
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS turns_session ON turns(session, id)"
            )
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

            # Structured memory records: profile facts, episodic notes, user facts
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_records (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('profile', 'fact', 'note')),
                    text TEXT NOT NULL,
                    category TEXT,
                    source TEXT NOT NULL DEFAULT 'user_explicit',
                    source_ids TEXT NOT NULL DEFAULT '[]',
                    trust TEXT NOT NULL DEFAULT 'user_explicit',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    valid_from TEXT,
                    valid_to TEXT,
                    supersedes_id TEXT,
                    superseded_by_id TEXT,
                    revision INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'deleted')),
                    deleted_at TEXT
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memory_scope_status
                ON memory_records(scope, status, kind)
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memory_supersedes
                ON memory_records(supersedes_id)
            """)

            # Revision history
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    category TEXT,
                    updated_at TEXT NOT NULL,
                    reason TEXT,
                    source TEXT,
                    source_ids TEXT NOT NULL DEFAULT '[]',
                    FOREIGN KEY (record_id) REFERENCES memory_records(id)
                )
            """)

            # Tombstones / Deletions log
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_deletions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    deleted_at TEXT NOT NULL,
                    reason TEXT
                )
            """)

            # SQLite FTS5 table for fast lexical retrieval
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    record_id UNINDEXED,
                    scope UNINDEXED,
                    kind UNINDEXED,
                    text,
                    category
                )
            """)

    # --- Turn history & state ---

    def history(
        self, session: str, limit: int = 20
    ) -> builtins.list[dict[str, Any]]:
        """Retrieve historical turn messages for a session, ordered chronologically."""
        cursor = self._conn.execute(
            """
            SELECT messages FROM (
                SELECT id, messages FROM turns WHERE session = ? ORDER BY id DESC LIMIT ?
            ) ORDER BY id
            """,
            (session, limit),
        )
        rows = cursor.fetchall()
        result: builtins.list[dict[str, Any]] = []
        for row in rows:
            try:
                parsed = json.loads(row["messages"])
                if isinstance(parsed, list):
                    result.extend(parsed)
                elif isinstance(parsed, dict):
                    result.append(parsed)
            except Exception:
                continue
        return result

    def reply(self, session: str, request_id: str) -> str | None:
        """Check for existing reply for request deduplication."""
        cursor = self._conn.execute(
            "SELECT reply FROM turns WHERE session = ? AND request_id = ?",
            (session, request_id),
        )
        row = cursor.fetchone()
        if row:
            return str(row["reply"])
        return None

    def save(
        self,
        session: str,
        request_id: str,
        messages: builtins.list[Any],
        reply: str,
    ) -> None:
        """Record turn messages and assistant reply with request_id deduplication key."""
        serialized_messages: builtins.list[dict[str, Any]] = []
        for msg in messages:
            if hasattr(msg, "model_dump"):
                serialized_messages.append(msg.model_dump())
            elif hasattr(msg, "__dict__"):
                serialized_messages.append(dict(msg.__dict__))
            elif isinstance(msg, dict):
                serialized_messages.append(msg)
            else:
                serialized_messages.append({"content": str(msg)})

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO turns(session, request_id, messages, reply)
                VALUES (?, ?, ?, ?)
                """,
                (session, request_id, json.dumps(serialized_messages), reply),
            )

    def clear(self, session: str) -> None:
        """Clear turn conversation history for a specific session."""
        with self._conn:
            self._conn.execute("DELETE FROM turns WHERE session = ?", (session,))

    def get_state(self, key: str) -> str | None:
        """Retrieve key-value state."""
        cursor = self._conn.execute("SELECT value FROM state WHERE key = ?", (key,))
        row = cursor.fetchone()
        if row:
            return str(row["value"])
        return None

    def getState(self, key: str) -> str | None:
        """CamelCase alias for get_state."""
        return self.get_state(key)

    def set_state(self, key: str, value: str) -> None:
        """Store or update key-value state."""
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO state(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def setState(self, key: str, value: str) -> None:
        """CamelCase alias for set_state."""
        self.set_state(key, value)

    def close(self) -> None:
        """Close SQLite database connection."""
        self._conn.close()

    # --- Structured Memory Methods ---

    def remember(
        self, scope: str, input_data: RememberInput | dict[str, Any]
    ) -> MemoryRecord:
        """Store a new durable memory record with provenance and indexing."""
        inp = (
            input_data
            if isinstance(input_data, RememberInput)
            else RememberInput.model_validate(input_data)
        )
        text = inp.text.strip()
        if not text:
            raise ValueError("Memory text must not be empty")

        rec_id = generate_memory_id()
        kind = inp.kind
        now = datetime.now(UTC).isoformat()
        source = inp.source
        source_ids = json.dumps(inp.source_ids)
        trust = inp.trust
        category = inp.category.strip() if inp.category and inp.category.strip() else None
        valid_from = inp.valid_from or now

        revision = 1
        supersedes_id: str | None = None

        with self._conn:
            if inp.supersedes_id:
                target_id = inp.supersedes_id.strip()
                cursor = self._conn.execute(
                    "SELECT * FROM memory_records WHERE id = ? AND scope = ?",
                    (target_id, scope),
                )
                old_row = cursor.fetchone()
                if old_row and old_row["status"] == "active":
                    supersedes_id = target_id
                    revision = int(old_row["revision"]) + 1
                    self._conn.execute(
                        """
                        UPDATE memory_records
                        SET status = 'superseded', superseded_by_id = ?, valid_to = ?, updated_at = ?
                        WHERE id = ? AND scope = ?
                        """,
                        (rec_id, now, now, target_id, scope),
                    )
                    self._conn.execute(
                        "DELETE FROM memory_fts WHERE record_id = ?",
                        (target_id,),
                    )

            self._conn.execute(
                """
                INSERT INTO memory_records (
                    id, scope, kind, text, category, source, source_ids, trust,
                    created_at, updated_at, valid_from, valid_to, supersedes_id,
                    superseded_by_id, revision, status, deleted_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, NULL, ?,
                    NULL, ?, 'active', NULL
                )
                """,
                (
                    rec_id,
                    scope,
                    kind,
                    text,
                    category,
                    source,
                    source_ids,
                    trust,
                    now,
                    now,
                    valid_from,
                    supersedes_id,
                    revision,
                ),
            )

            self._conn.execute(
                """
                INSERT INTO memory_fts (record_id, scope, kind, text, category)
                VALUES (?, ?, ?, ?, ?)
                """,
                (rec_id, scope, kind, text, category or ""),
            )

        rec = self.get(scope, rec_id, include_inactive=True)
        if rec is None:
            raise RuntimeError(f"Failed to create memory record {rec_id}")
        return rec

    def revise(
        self, scope: str, id: str, input_data: ReviseInput | dict[str, Any]
    ) -> MemoryRecord:
        """Update an existing memory record, preserving revision history with CAS check."""
        inp = (
            input_data
            if isinstance(input_data, ReviseInput)
            else ReviseInput.model_validate(input_data)
        )
        text = inp.text.strip()
        if not text:
            raise ValueError("Updated memory text must not be empty")

        now = datetime.now(UTC).isoformat()
        target_id = id.strip()

        with self._conn:
            cursor = self._conn.execute(
                "SELECT * FROM memory_records WHERE id = ? AND scope = ?",
                (target_id, scope),
            )
            old_row = cursor.fetchone()

            if not old_row or old_row["status"] != "active":
                raise ValueError(
                    f"Memory record not found or not active: {target_id}"
                )

            current_rev = int(old_row["revision"])
            if (
                inp.expected_revision is not None
                and current_rev != inp.expected_revision
            ):
                raise ValueError(
                    f"Version conflict: expected revision {inp.expected_revision}, "
                    f"but found revision {current_rev}"
                )

            # Record old state in revisions history
            self._conn.execute(
                """
                INSERT INTO memory_revisions (
                    record_id, revision, text, category, updated_at, reason, source, source_ids
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    old_row["id"],
                    old_row["revision"],
                    old_row["text"],
                    old_row["category"],
                    old_row["updated_at"],
                    inp.reason,
                    old_row["source"],
                    old_row["source_ids"],
                ),
            )

            new_category = (
                (inp.category.strip() if inp.category.strip() else None)
                if inp.category is not None
                else old_row["category"]
            )
            new_source = inp.source or old_row["source"]
            new_source_ids = (
                json.dumps(inp.source_ids)
                if inp.source_ids is not None
                else old_row["source_ids"]
            )
            new_revision = current_rev + 1

            self._conn.execute(
                """
                UPDATE memory_records
                SET text = ?, category = ?, revision = ?, updated_at = ?, source = ?, source_ids = ?
                WHERE id = ? AND scope = ?
                """,
                (
                    text,
                    new_category,
                    new_revision,
                    now,
                    new_source,
                    new_source_ids,
                    target_id,
                    scope,
                ),
            )

            # Update FTS5
            self._conn.execute(
                "DELETE FROM memory_fts WHERE record_id = ?", (target_id,)
            )
            self._conn.execute(
                """
                INSERT INTO memory_fts (record_id, scope, kind, text, category)
                VALUES (?, ?, ?, ?, ?)
                """,
                (target_id, scope, old_row["kind"], text, new_category or ""),
            )

        rec = self.get(scope, target_id, include_inactive=True)
        if rec is None:
            raise RuntimeError(f"Failed to retrieve revised record {target_id}")
        return rec

    def supersede(
        self, scope: str, old_id: str, input_data: RememberInput | dict[str, Any]
    ) -> MemoryRecord:
        """Replace an existing memory record with a newer version."""
        if isinstance(input_data, RememberInput):
            data = input_data.model_dump()
        else:
            data = dict(input_data)
        data["supersedes_id"] = old_id
        return self.remember(scope, data)

    def forget(self, scope: str, id: str, reason: str | None = None) -> bool:
        """Mark memory record as deleted, delete from FTS index, and record tombstone."""
        target_id = id.strip()
        now = datetime.now(UTC).isoformat()

        with self._conn:
            cursor = self._conn.execute(
                "SELECT id, status FROM memory_records WHERE id = ? AND scope = ?",
                (target_id, scope),
            )
            row = cursor.fetchone()

            if not row or row["status"] == "deleted":
                return False

            self._conn.execute(
                """
                UPDATE memory_records
                SET status = 'deleted', deleted_at = ?, updated_at = ?
                WHERE id = ? AND scope = ?
                """,
                (now, now, target_id, scope),
            )

            self._conn.execute(
                "DELETE FROM memory_fts WHERE record_id = ?", (target_id,)
            )

            self._conn.execute(
                """
                INSERT INTO memory_deletions (record_id, scope, deleted_at, reason)
                VALUES (?, ?, ?, ?)
                """,
                (target_id, scope, now, reason),
            )

        return True

    def get(
        self, scope: str, id: str, include_inactive: bool = False
    ) -> MemoryRecord | None:
        """Retrieve a memory record by scope and ID."""
        target_id = id.strip()
        query = (
            "SELECT * FROM memory_records WHERE id = ? AND scope = ?"
            if include_inactive
            else "SELECT * FROM memory_records WHERE id = ? AND scope = ? AND status = 'active'"
        )
        cursor = self._conn.execute(query, (target_id, scope))
        row = cursor.fetchone()
        if not row:
            return None
        return _row_to_memory_record(row)

    def search(
        self,
        scope: str,
        query: str,
        options: SearchOptions | dict[str, Any] | None = None,
    ) -> builtins.list[MemoryRecord]:
        """Search active memories by lexical FTS5 BM25 relevance or recency fallback."""
        opts = (
            options
            if isinstance(options, SearchOptions)
            else SearchOptions.model_validate(options or {})
        )
        limit = max(1, min(opts.limit, 100))
        sanitized = sanitize_fts5_query(query)

        if sanitized:
            try:
                cursor = self._conn.execute(
                    """
                    SELECT m.*, f.rank
                    FROM memory_records m
                    JOIN memory_fts f ON m.id = f.record_id
                    WHERE m.scope = ? AND m.status = 'active'
                      AND (? IS NULL OR m.kind = ?)
                      AND (? IS NULL OR m.category = ?)
                      AND memory_fts MATCH ?
                    ORDER BY f.rank
                    LIMIT ?
                    """,
                    (
                        scope,
                        opts.kind,
                        opts.kind,
                        opts.category,
                        opts.category,
                        sanitized,
                        limit,
                    ),
                )
                rows = cursor.fetchall()
                return [_row_to_memory_record(r) for r in rows]
            except Exception:
                # Fallback to recency listing if driver-level FTS exception occurs
                pass

        # Fallback: list active records filtered by kind and category, ordered by recency
        cursor = self._conn.execute(
            """
            SELECT *
            FROM memory_records
            WHERE scope = ? AND status = 'active'
              AND (? IS NULL OR kind = ?)
              AND (? IS NULL OR category = ?)
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (scope, opts.kind, opts.kind, opts.category, opts.category, limit),
        )
        rows = cursor.fetchall()
        return [_row_to_memory_record(r) for r in rows]

    def list(
        self, scope: str, options: ListOptions | dict[str, Any] | None = None
    ) -> builtins.list[MemoryRecord]:
        """List memory records filtered by status and kind, ordered by updated_at desc."""
        opts = (
            options
            if isinstance(options, ListOptions)
            else ListOptions.model_validate(options or {})
        )
        status = opts.status
        limit = max(1, min(opts.limit, 200))

        cursor = self._conn.execute(
            """
            SELECT *
            FROM memory_records
            WHERE scope = ? AND status = ?
              AND (? IS NULL OR kind = ?)
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (scope, status, opts.kind, opts.kind, limit),
        )
        rows = cursor.fetchall()
        return [_row_to_memory_record(r) for r in rows]

    def get_profile(
        self, scope: str, limit: int = 10
    ) -> builtins.list[MemoryRecord]:
        """Retrieve active profile traits for a session."""
        return self.list(
            scope, ListOptions(kind="profile", status="active", limit=limit)
        )

    def getProfile(
        self, scope: str, limit: int = 10
    ) -> builtins.list[MemoryRecord]:
        """CamelCase alias for get_profile."""
        return self.get_profile(scope, limit)

    def assemble_context(
        self,
        scope: str,
        query: str | None = None,
        options: ContextAssemblyOptions | dict[str, Any] | None = None,
    ) -> str:
        """Assemble bounded and labeled memory context formatted for the system prompt."""
        opts = (
            options
            if isinstance(options, ContextAssemblyOptions)
            else ContextAssemblyOptions.model_validate(options or {})
        )

        max_entries = opts.max_entries
        max_chars = opts.max_chars
        max_profile_entries = opts.max_profile_entries
        max_search_entries = opts.max_search_entries

        profile_records = self.get_profile(scope, max_profile_entries)
        seen_ids = {r.id for r in profile_records}

        search_records: builtins.list[MemoryRecord] = []
        if query and query.strip():
            raw_search = self.search(
                scope, query.strip(), SearchOptions(limit=max_search_entries)
            )
            search_records = [r for r in raw_search if r.id not in seen_ids]

        total_count = len(profile_records) + len(search_records)
        if total_count == 0:
            return ""

        lines: builtins.list[str] = [
            "## Active Memory Context",
            "The following memory records are retrieved from long-term storage for this session. "
            "Use their evidence IDs when referencing facts.",
        ]

        if profile_records:
            lines.append("### Profile")
            for r in profile_records[:max_entries]:
                cat_part = f", cat: {r.category}" if r.category else ""
                lines.append(
                    f"- [Evidence: {r.id}] ({r.kind}, rev {r.revision}{cat_part}): {r.text}"
                )

        remaining_slots = max(0, max_entries - len(profile_records))
        if search_records and remaining_slots > 0:
            lines.append("### Relevant Facts & Notes")
            for r in search_records[:remaining_slots]:
                date_part = (
                    f", updated {r.updated_at[:10]}" if r.updated_at else ""
                )
                cat_part = f", cat: {r.category}" if r.category else ""
                lines.append(
                    f"- [Evidence: {r.id}] ({r.kind}, rev {r.revision}{date_part}{cat_part}): {r.text}"
                )

        result = "\n".join(lines)
        if len(result) > max_chars:
            result = result[: max_chars - 3] + "..."
        return result

    def assembleContext(
        self,
        scope: str,
        query: str | None = None,
        options: ContextAssemblyOptions | dict[str, Any] | None = None,
    ) -> str:
        """CamelCase alias for assemble_context."""
        return self.assemble_context(scope, query, options)


__all__ = ["SqliteMemory", "generate_memory_id"]
