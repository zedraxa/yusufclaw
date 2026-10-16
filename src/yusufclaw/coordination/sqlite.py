"""Transactional, append-only local coordination with FTS5/BM25 discovery."""

import asyncio
import hashlib
import re
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .models import Artifact, ArtifactInput, Event, Evidence, PublishInput, SearchHit, Visit
from .protocols import Verifier


class SqliteCoordination:
    """All async operations run off-loop; a thread lock protects whole transactions."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA recursive_triggers=ON")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE, scope TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_scope ON events(scope, sequence);
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, payload TEXT NOT NULL,
                content BLOB NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS coordination_fts USING fts5(
                id UNINDEXED, scope UNINDEXED, kind UNINDEXED, text
            );
            CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
                BEGIN SELECT RAISE(ABORT, 'append-only events'); END;
            CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
                BEGIN SELECT RAISE(ABORT, 'append-only events'); END;
            CREATE TRIGGER IF NOT EXISTS artifacts_no_update BEFORE UPDATE ON artifacts
                BEGIN SELECT RAISE(ABORT, 'immutable artifacts'); END;
            CREATE TRIGGER IF NOT EXISTS artifacts_no_delete BEFORE DELETE ON artifacts
                BEGIN SELECT RAISE(ABORT, 'immutable artifacts'); END;
        """)

    async def _run[T](self, operation: Callable[[], T]) -> T:
        def locked() -> T:
            with self._lock:
                return operation()

        return await asyncio.to_thread(locked)

    async def close(self) -> None:
        await self._run(self._conn.close)

    async def put_artifact(self, item: ArtifactInput, content: bytes) -> Artifact:
        artifact = Artifact(
            **item.model_dump(),
            id=f"art_{uuid4().hex}",
            sha256=hashlib.sha256(content).hexdigest(),
            size=len(content),
            created_at=datetime.now(UTC),
        )

        def write() -> Artifact:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO artifacts VALUES (?, ?, ?, ?)",
                    (artifact.id, item.scope, artifact.model_dump_json(), content),
                )
                self._conn.execute(
                    "INSERT INTO coordination_fts VALUES (?, ?, ?, ?)",
                    (
                        artifact.id,
                        item.scope,
                        "artifact",
                        " ".join((item.name, item.description, item.media_type)),
                    ),
                )
            return artifact

        return await self._run(write)

    def _artifact(self, ref: str, scope: str) -> tuple[Artifact, bytes]:
        row = self._conn.execute(
            "SELECT payload, content FROM artifacts WHERE id=? AND scope=?", (ref, scope)
        ).fetchone()
        if row is None:
            raise KeyError(ref)
        artifact = Artifact.model_validate_json(row["payload"])
        content = bytes(row["content"])
        if hashlib.sha256(content).hexdigest() != artifact.sha256 or len(content) != artifact.size:
            raise ValueError("Artifact integrity check failed")
        return artifact, content

    async def publish(self, item: PublishInput) -> Event:
        def write() -> Event:
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                for ref in item.artifact_ids:
                    self._artifact(ref, item.scope)
                for ref in item.source_ids:
                    if (
                        self._conn.execute(
                            "SELECT 1 FROM events WHERE id=? AND scope=?", (ref, item.scope)
                        ).fetchone()
                        is None
                    ):
                        raise KeyError(ref)
                sequence = self._conn.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events"
                ).fetchone()[0]
                event = Event(
                    **item.model_dump(),
                    id=f"evt_{uuid4().hex}",
                    sequence=sequence,
                    created_at=datetime.now(UTC),
                )
                self._conn.execute(
                    "INSERT INTO events VALUES (?, ?, ?, ?)",
                    (sequence, event.id, item.scope, event.model_dump_json()),
                )
                self._conn.execute(
                    "INSERT INTO coordination_fts VALUES (?, ?, ?, ?)",
                    (event.id, item.scope, "event", item.text),
                )
            return event

        return await self._run(write)

    async def search(self, query: str, *, scope: str, limit: int = 20) -> list[SearchHit]:
        self._limit(limit)
        # Treat user input as literal words, never FTS operators or SQL.
        match = " OR ".join(f'"{word}"' for word in re.findall(r"\w+", query))
        if not match:
            return []

        def read() -> list[SearchHit]:
            rows = self._conn.execute(
                """
                SELECT id, kind, text, bm25(coordination_fts) AS rank
                FROM coordination_fts WHERE coordination_fts MATCH ? AND scope=?
                ORDER BY rank, id LIMIT ?
            """,
                (match, scope, limit),
            ).fetchall()
            return [
                SearchHit(id=r["id"], kind=r["kind"], text=r["text"], score=-r["rank"])
                for r in rows
            ]

        return await self._run(read)

    async def list_events(self, *, scope: str, after: int = 0, limit: int = 100) -> list[Event]:
        self._limit(limit)
        if after < 0:
            raise ValueError("after must be nonnegative")

        def read() -> list[Event]:
            rows = self._conn.execute(
                "SELECT payload FROM events WHERE scope=? AND sequence>? ORDER BY sequence LIMIT ?",
                (scope, after, limit),
            ).fetchall()
            return [Event.model_validate_json(r["payload"]) for r in rows]

        return await self._run(read)

    async def visit(self, ref: str, *, scope: str, verifier: Verifier | None = None) -> Visit:
        def read() -> Evidence:
            row = self._conn.execute(
                "SELECT payload FROM events WHERE id=? AND scope=?", (ref, scope)
            ).fetchone()
            event = Event.model_validate_json(row["payload"]) if row else None
            refs = event.artifact_ids if event else (ref,)
            pairs = [self._artifact(artifact_id, scope) for artifact_id in refs]
            return Evidence(
                event=event, artifacts=tuple(a for a, _ in pairs), blobs=tuple(b for _, b in pairs)
            )

        evidence = await self._run(read)
        if verifier is None:
            return Visit(evidence=evidence)
        # Fail closed: exceptions propagate; a verdict cannot cite unseen evidence.
        verdict = await verifier.verify(evidence)
        available = {a.id for a in evidence.artifacts}
        if evidence.event:
            available.add(evidence.event.id)
        if not set(verdict.checked_ids) <= available:
            raise ValueError("Verifier cited evidence outside this visit")
        return Visit(evidence=evidence, verification=verdict)

    @staticmethod
    def _limit(limit: int) -> None:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
