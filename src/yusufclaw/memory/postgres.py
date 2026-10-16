"""PostgreSQL and pgvector async memory implementation using SQLAlchemy and psycopg."""

import builtins
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from yusufclaw.memory.context import rank_memories
from yusufclaw.memory.fts import sanitize_fts5_query
from yusufclaw.memory.models import (
    ContextAssemblyOptions,
    ListOptions,
    MemoryKind,
    MemoryRecord,
    RememberInput,
    ReviseInput,
    SearchOptions,
)
from yusufclaw.memory.sqlite import generate_memory_id


class Base(DeclarativeBase):
    """Declarative base for memory schema."""

    pass


class TurnModel(Base):
    """Turn conversation history and assistant replies."""

    __tablename__ = "turns"
    __table_args__ = (
        UniqueConstraint("session", "request_id", name="uq_turns_session_req"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    reply: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(
        String(64),
        default=lambda: datetime.now(UTC).isoformat(),
        nullable=False,
    )


class StateModel(Base):
    """Key-value persistence table."""

    __tablename__ = "state"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class MemoryRecordModel(Base):
    """Structured durable memory records with provenance and lifecycle."""

    __tablename__ = "memory_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(
        String(64), default="user_explicit", nullable=False
    )
    source_ids: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    trust: Mapped[str] = mapped_column(
        String(32), default="user_explicit", nullable=False
    )
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_from: Mapped[str | None] = mapped_column(String(64), nullable=True)
    valid_to: Mapped[str | None] = mapped_column(String(64), nullable=True)
    supersedes_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    superseded_by_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="active", nullable=False, index=True
    )
    deleted_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1536), nullable=True
    )


class MemoryRevisionModel(Base):
    """Audit table storing previous revisions of edited records."""

    __tablename__ = "memory_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("memory_records.id"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_ids: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )


class MemoryDeletionModel(Base):
    """Audit table storing tombstones for deleted records."""

    __tablename__ = "memory_deletions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(255), nullable=False)
    deleted_at: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


def _to_memory_record(model: MemoryRecordModel) -> MemoryRecord:
    """Map SQLAlchemy model to Pydantic MemoryRecord."""
    source_ids = (
        model.source_ids
        if isinstance(model.source_ids, list)
        else json.loads(str(model.source_ids or "[]"))
    )
    raw_emb = model.embedding
    embedding: list[float] | None = None
    if raw_emb is not None:
        try:
            embedding = [float(x) for x in raw_emb]
        except Exception:
            embedding = None

    return MemoryRecord(
        id=model.id,
        scope=model.scope,
        kind=model.kind,  # type: ignore[arg-type]
        text=model.text,
        category=model.category,
        source=model.source,
        source_ids=source_ids,
        trust=model.trust,  # type: ignore[arg-type]
        created_at=model.created_at,
        updated_at=model.updated_at,
        valid_from=model.valid_from,
        valid_to=model.valid_to,
        supersedes_id=model.supersedes_id,
        superseded_by_id=model.superseded_by_id,
        revision=model.revision,
        status=model.status,  # type: ignore[arg-type]
        deleted_at=model.deleted_at,
        embedding=embedding,
    )


class PostgresMemory:
    """Asynchronous PostgreSQL and pgvector structured memory store."""

    def __init__(
        self,
        database_url: str | None = None,
        engine: AsyncEngine | None = None,
        vector_dimension: int = 1536,
    ) -> None:
        self.vector_dimension = vector_dimension

        if engine is not None:
            self.engine = engine
        elif database_url:
            # Normalize url for psycopg async driver
            url = database_url
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+psycopg://", 1)
            elif url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql+psycopg://", 1)
            self.engine = create_async_engine(url, echo=False)
        else:
            raise ValueError("Either database_url or engine must be provided")

        self.session_factory = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )

    async def init_db(self) -> None:
        """Create database tables and enable vector extension if on PostgreSQL."""
        async with self.engine.begin() as conn:
            if self.engine.dialect.name == "postgresql":
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        """Dispose of the underlying SQLAlchemy async engine."""
        await self.engine.dispose()

    # --- Turn history & state ---

    async def history(
        self, session: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Retrieve recent conversation messages for a session."""
        async with self.session_factory() as s:
            stmt = (
                select(TurnModel.messages)
                .where(TurnModel.session == session)
                .order_by(TurnModel.id.desc())
                .limit(limit)
            )
            rows = (await s.scalars(stmt)).all()
            result: list[dict[str, Any]] = []
            for m in reversed(rows):
                if isinstance(m, list):
                    result.extend(m)
                elif isinstance(m, dict):
                    result.append(m)
            return result

    async def reply(self, session: str, request_id: str) -> str | None:
        """Check for existing reply for request deduplication."""
        async with self.session_factory() as s:
            stmt = select(TurnModel.reply).where(
                and_(
                    TurnModel.session == session,
                    TurnModel.request_id == request_id,
                )
            )
            res = (await s.scalars(stmt)).first()
            return str(res) if res is not None else None

    async def save(
        self,
        session: str,
        request_id: str,
        messages: Sequence[Any],
        reply: str,
    ) -> None:
        """Record turn messages and assistant reply."""
        serialized: list[dict[str, Any]] = []
        for msg in messages:
            if hasattr(msg, "model_dump"):
                serialized.append(msg.model_dump())
            elif hasattr(msg, "__dict__"):
                serialized.append(dict(msg.__dict__))
            elif isinstance(msg, dict):
                serialized.append(msg)
            else:
                serialized.append({"content": str(msg)})

        async with self.session_factory() as s:
            async with s.begin():
                turn = TurnModel(
                    session=session,
                    request_id=request_id,
                    messages=serialized,
                    reply=reply,
                )
                s.add(turn)

    async def clear(self, session: str) -> None:
        """Clear turn history for a session."""
        async with self.session_factory() as s:
            async with s.begin():
                stmt = select(TurnModel).where(TurnModel.session == session)
                records = (await s.scalars(stmt)).all()
                for r in records:
                    await s.delete(r)

    async def get_state(self, key: str) -> str | None:
        """Retrieve key-value state."""
        async with self.session_factory() as s:
            stmt = select(StateModel.value).where(StateModel.key == key)
            res = (await s.scalars(stmt)).first()
            return str(res) if res is not None else None

    async def set_state(self, key: str, value: str) -> None:
        """Persist or update key-value state."""
        async with self.session_factory() as s:
            async with s.begin():
                stmt = select(StateModel).where(StateModel.key == key)
                existing = (await s.scalars(stmt)).first()
                if existing:
                    existing.value = value
                else:
                    s.add(StateModel(key=key, value=value))

    # --- Structured Memory Operations ---

    async def remember(
        self, scope: str, input_data: RememberInput | dict[str, Any]
    ) -> MemoryRecord:
        """Store a durable memory record with provenance and indexing."""
        inp = (
            input_data
            if isinstance(input_data, RememberInput)
            else RememberInput.model_validate(input_data)
        )
        text_val = inp.text.strip()
        if not text_val:
            raise ValueError("Memory text must not be empty")

        rec_id = generate_memory_id()
        now = datetime.now(UTC).isoformat()
        valid_from = inp.valid_from or now
        category = (
            inp.category.strip()
            if inp.category and inp.category.strip()
            else None
        )
        revision = 1
        supersedes_id: str | None = None

        async with self.session_factory() as s:
            async with s.begin():
                if inp.supersedes_id:
                    target_id = inp.supersedes_id.strip()
                    stmt = select(MemoryRecordModel).where(
                        and_(
                            MemoryRecordModel.id == target_id,
                            MemoryRecordModel.scope == scope,
                        )
                    )
                    old_model = (await s.scalars(stmt)).first()
                    if old_model and old_model.status == "active":
                        supersedes_id = target_id
                        revision = old_model.revision + 1
                        old_model.status = "superseded"
                        old_model.superseded_by_id = rec_id
                        old_model.valid_to = now
                        old_model.updated_at = now

                record_model = MemoryRecordModel(
                    id=rec_id,
                    scope=scope,
                    kind=inp.kind,
                    text=text_val,
                    category=category,
                    source=inp.source,
                    source_ids=inp.source_ids,
                    trust=inp.trust,
                    created_at=now,
                    updated_at=now,
                    valid_from=valid_from,
                    valid_to=None,
                    supersedes_id=supersedes_id,
                    superseded_by_id=None,
                    revision=revision,
                    status="active",
                    deleted_at=None,
                    embedding=inp.embedding,
                )
                s.add(record_model)

        rec = await self.get(scope, rec_id, include_inactive=True)
        if rec is None:
            raise RuntimeError(f"Failed to create memory record {rec_id}")
        return rec

    async def revise(
        self, scope: str, id: str, input_data: ReviseInput | dict[str, Any]
    ) -> MemoryRecord:
        """Revise an existing record, preserving revision history with CAS check."""
        inp = (
            input_data
            if isinstance(input_data, ReviseInput)
            else ReviseInput.model_validate(input_data)
        )
        text_val = inp.text.strip()
        if not text_val:
            raise ValueError("Updated memory text must not be empty")

        now = datetime.now(UTC).isoformat()
        target_id = id.strip()

        async with self.session_factory() as s:
            async with s.begin():
                stmt = select(MemoryRecordModel).where(
                    and_(
                        MemoryRecordModel.id == target_id,
                        MemoryRecordModel.scope == scope,
                    )
                )
                old_model = (await s.scalars(stmt)).first()

                if not old_model or old_model.status != "active":
                    raise ValueError(
                        f"Memory record not found or not active: {target_id}"
                    )

                if (
                    inp.expected_revision is not None
                    and old_model.revision != inp.expected_revision
                ):
                    raise ValueError(
                        f"Version conflict: expected revision {inp.expected_revision}, "
                        f"but found revision {old_model.revision}"
                    )

                # Record historical revision
                rev_model = MemoryRevisionModel(
                    record_id=old_model.id,
                    revision=old_model.revision,
                    text=old_model.text,
                    category=old_model.category,
                    updated_at=old_model.updated_at,
                    reason=inp.reason,
                    source=old_model.source,
                    source_ids=old_model.source_ids,
                )
                s.add(rev_model)

                new_cat = (
                    (inp.category.strip() if inp.category.strip() else None)
                    if inp.category is not None
                    else old_model.category
                )
                new_src = inp.source or old_model.source
                new_src_ids = (
                    inp.source_ids
                    if inp.source_ids is not None
                    else old_model.source_ids
                )

                old_model.text = text_val
                old_model.category = new_cat
                old_model.revision = old_model.revision + 1
                old_model.updated_at = now
                old_model.source = new_src
                old_model.source_ids = new_src_ids
                if inp.embedding is not None:
                    old_model.embedding = inp.embedding

        rec = await self.get(scope, target_id, include_inactive=True)
        if rec is None:
            raise RuntimeError(f"Failed to retrieve revised record {target_id}")
        return rec

    async def supersede(
        self,
        scope: str,
        old_id: str,
        input_data: RememberInput | dict[str, Any],
    ) -> MemoryRecord:
        """Supersede an older record with a newer version."""
        if isinstance(input_data, RememberInput):
            data = input_data.model_dump()
        else:
            data = dict(input_data)
        data["supersedes_id"] = old_id
        return await self.remember(scope, data)

    async def forget(
        self, scope: str, id: str, reason: str | None = None
    ) -> bool:
        """Soft-delete a record and create a tombstone log."""
        target_id = id.strip()
        now = datetime.now(UTC).isoformat()

        async with self.session_factory() as s:
            async with s.begin():
                stmt = select(MemoryRecordModel).where(
                    and_(
                        MemoryRecordModel.id == target_id,
                        MemoryRecordModel.scope == scope,
                    )
                )
                rec = (await s.scalars(stmt)).first()
                if not rec or rec.status == "deleted":
                    return False

                rec.status = "deleted"
                rec.deleted_at = now
                rec.updated_at = now

                tombstone = MemoryDeletionModel(
                    record_id=target_id,
                    scope=scope,
                    deleted_at=now,
                    reason=reason,
                )
                s.add(tombstone)

        return True

    async def get(
        self, scope: str, id: str, include_inactive: bool = False
    ) -> MemoryRecord | None:
        """Retrieve record by ID and scope."""
        target_id = id.strip()
        async with self.session_factory() as s:
            conditions = [
                MemoryRecordModel.id == target_id,
                MemoryRecordModel.scope == scope,
            ]
            if not include_inactive:
                conditions.append(MemoryRecordModel.status == "active")

            stmt = select(MemoryRecordModel).where(and_(*conditions))
            model = (await s.scalars(stmt)).first()
            return _to_memory_record(model) if model else None

    async def search(
        self,
        scope: str,
        query: str,
        options: SearchOptions | dict[str, Any] | None = None,
    ) -> list[MemoryRecord]:
        """Full-text lexical search supporting PostgreSQL to_tsvector and SQLite fallback."""
        opts = (
            options
            if isinstance(options, SearchOptions)
            else SearchOptions.model_validate(options or {})
        )
        limit = max(1, min(opts.limit, 100))
        sanitized = sanitize_fts5_query(query)

        async with self.session_factory() as s:
            conditions = [
                MemoryRecordModel.scope == scope,
                MemoryRecordModel.status == "active",
            ]
            if opts.kind:
                conditions.append(MemoryRecordModel.kind == opts.kind)
            if opts.category:
                conditions.append(MemoryRecordModel.category == opts.category)

            # If connected to PostgreSQL, execute to_tsvector / ts_rank query
            if self.engine.dialect.name == "postgresql" and sanitized:
                try:
                    ts_query = func.plainto_tsquery("simple", query)
                    ts_vector = func.to_tsvector(
                        "simple", MemoryRecordModel.text
                    )
                    stmt = (
                        select(MemoryRecordModel)
                        .where(and_(*conditions, ts_vector.op("@@")(ts_query)))
                        .order_by(func.ts_rank(ts_vector, ts_query).desc())
                        .limit(limit)
                    )
                    rows = (await s.scalars(stmt)).all()
                    return [_to_memory_record(r) for r in rows]
                except Exception:
                    pass

            # SQLite / standard fallback using LIKE on extracted words
            tokens = query.split()
            if tokens:
                like_conditions = [
                    MemoryRecordModel.text.ilike(f"%{tok}%") for tok in tokens
                ]
                match_stmt = (
                    select(MemoryRecordModel)
                    .where(and_(*conditions, or_(*like_conditions)))
                    .order_by(MemoryRecordModel.updated_at.desc())
                    .limit(limit)
                )
                matched_rows = (await s.scalars(match_stmt)).all()
                if matched_rows:
                    return [_to_memory_record(r) for r in matched_rows]

            # Recency fallback
            fallback_stmt = (
                select(MemoryRecordModel)
                .where(and_(*conditions))
                .order_by(MemoryRecordModel.updated_at.desc())
                .limit(limit)
            )
            fallback_rows = (await s.scalars(fallback_stmt)).all()
            return [_to_memory_record(r) for r in fallback_rows]

    async def search_vector(
        self,
        scope: str,
        embedding: list[float],
        limit: int = 10,
        kind: MemoryKind | None = None,
        category: str | None = None,
    ) -> list[MemoryRecord]:
        """Perform semantic vector similarity search using pgvector cosine distance."""
        limit = max(1, min(limit, 100))
        async with self.session_factory() as s:
            conditions = [
                MemoryRecordModel.scope == scope,
                MemoryRecordModel.status == "active",
                MemoryRecordModel.embedding.isnot(None),
            ]
            if kind:
                conditions.append(MemoryRecordModel.kind == kind)
            if category:
                conditions.append(MemoryRecordModel.category == category)

            if self.engine.dialect.name == "postgresql":
                stmt = (
                    select(MemoryRecordModel)
                    .where(and_(*conditions))
                    .order_by(
                        MemoryRecordModel.embedding.cosine_distance(embedding)
                    )
                    .limit(limit)
                )
                rows = (await s.scalars(stmt)).all()
                return [_to_memory_record(r) for r in rows]

            # In-memory cosine distance fallback for SQLite / offline tests
            stmt = select(MemoryRecordModel).where(and_(*conditions))
            all_records = (await s.scalars(stmt)).all()

            def _cosine_dist(a: list[float], b: list[float]) -> float:
                dot = sum(x * y for x, y in zip(a, b, strict=False))
                norm_a = sum(x * x for x in a) ** 0.5
                norm_b = sum(y * y for y in b) ** 0.5
                if norm_a == 0.0 or norm_b == 0.0:
                    return 1.0
                return float(1.0 - (dot / (norm_a * norm_b)))

            scored: list[tuple[float, MemoryRecordModel]] = []
            for r in all_records:
                if r.embedding:
                    dist = _cosine_dist(list(r.embedding), embedding)
                    scored.append((dist, r))

            scored.sort(key=lambda pair: pair[0])
            return [_to_memory_record(r) for _, r in scored[:limit]]

    async def list(
        self, scope: str, options: ListOptions | dict[str, Any] | None = None
    ) -> builtins.list[MemoryRecord]:
        """List records filtered by status and kind."""
        opts = (
            options
            if isinstance(options, ListOptions)
            else ListOptions.model_validate(options or {})
        )
        limit = max(1, min(opts.limit, 200))

        async with self.session_factory() as s:
            conditions = [
                MemoryRecordModel.scope == scope,
                MemoryRecordModel.status == opts.status,
            ]
            if opts.kind:
                conditions.append(MemoryRecordModel.kind == opts.kind)

            stmt = (
                select(MemoryRecordModel)
                .where(and_(*conditions))
                .order_by(MemoryRecordModel.updated_at.desc())
                .limit(limit)
            )
            rows = (await s.scalars(stmt)).all()
            return [_to_memory_record(r) for r in rows]

    async def get_profile(
        self, scope: str, limit: int = 10
    ) -> builtins.list[MemoryRecord]:
        """Retrieve active profile facts for a session."""
        return await self.list(
            scope, ListOptions(kind="profile", status="active", limit=limit)
        )

    async def assemble_context(
        self,
        scope: str,
        query: str | None = None,
        options: ContextAssemblyOptions | dict[str, Any] | None = None,
    ) -> str:
        """Assemble bounded, labeled memory context for model prompt."""
        opts = (
            options
            if isinstance(options, ContextAssemblyOptions)
            else ContextAssemblyOptions.model_validate(options or {})
        )

        if opts.max_entries <= 0 or opts.max_chars <= 0:
            return ""

        max_entries = opts.max_entries
        max_chars = opts.max_chars
        max_profile_entries = opts.max_profile_entries
        max_search_entries = opts.max_search_entries

        profile_records = rank_memories(
            await self.get_profile(scope, max(20, max_profile_entries)), opts
        )[:max(0, max_profile_entries)]
        seen_ids = {r.id for r in profile_records}

        search_records: builtins.list[MemoryRecord] = []
        if query and query.strip():
            raw_search = await self.search(
                scope, query.strip(), SearchOptions(limit=max(20, max_search_entries))
            )
            search_records = rank_memories(
                [r for r in raw_search if r.id not in seen_ids], opts
            )[:max(0, max_search_entries)]

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
            result = (result[: max_chars - 3] + "...") if max_chars >= 3 else result[:max_chars]
        return result


__all__ = [
    "Base",
    "MemoryDeletionModel",
    "MemoryRecordModel",
    "MemoryRevisionModel",
    "PostgresMemory",
    "StateModel",
    "TurnModel",
]
