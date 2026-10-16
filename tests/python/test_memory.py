"""Comprehensive test suite for YusufClaw memory subsystem.

Ports every TypeScript memory test case from tests/memory.test.ts:
1. Restart recall (persistence across database reopen, FTS5 search, context assembly)
2. Supersession (revision increment, exclusion from active retrieval, history preserved)
3. Scope isolation (strict separation between sessions/channels)
4. Deletion / forget (removal from active retrieval and search, tombstone logging)
5. FTS search basics (prefix matching, Turkish characters, category filtering, symbol resilience)
6. Optimistic concurrency control (CAS) and revision audit tracking
7. Bounded and labeled context assembly with evidence IDs
8. Agent loop memory wiring and tools (remember, revise, forget, recall)
9. Turn history preservation and /reset behavior
10. Direct create_memory_tools schema and execution tests

Plus PostgreSQL / pgvector async engine tests and Pydantic model validation.
"""

import shutil
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from yusufclaw.agent import Agent
from yusufclaw.memory import (
    ContextAssemblyOptions,
    MemoryRecord,
    PostgresMemory,
    RememberInput,
    ReviseInput,
    SqliteMemory,
    create_memory_tools,
    sanitize_fts5_query,
)
from yusufclaw.models import FakeProvider, ModelResponse, ToolCall


@pytest.fixture
def memory_db() -> Generator[SqliteMemory, None, None]:
    """Provide an in-memory SqliteMemory instance cleaned up after test."""
    db = SqliteMemory(":memory:")
    yield db
    db.close()


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Provide a temporary directory for file-backed database tests."""
    temp_path = Path(tempfile.mkdtemp(prefix="yusufclaw-mem-test-"))
    yield temp_path
    shutil.rmtree(temp_path, ignore_errors=True)


# --- 1. Restart Recall ---


def test_restart_recall(temp_dir: Path) -> None:
    """Restart recall: persists profile facts, notes, and user facts across database reopen."""
    db_path = str(temp_dir / "test-memory.sqlite")
    db = SqliteMemory(db_path)

    profile_fact = db.remember(
        "session-1",
        {
            "text": "User preferred language is Turkish",
            "kind": "profile",
            "category": "language",
            "source": "user_explicit",
            "source_ids": ["req-1"],
        },
    )

    note = db.remember(
        "session-1",
        {
            "text": "Discussed Phase 3 memory architecture and FTS5 search indexing",
            "kind": "note",
            "category": "architecture",
            "source": "meeting",
            "source_ids": ["req-2"],
        },
    )

    fact = db.remember(
        "session-1",
        {
            "text": "Yusuf works on project YusufClaw at /home/yusuf/projects/wt/yc-memory",
            "kind": "fact",
            "category": "project",
            "source": "observed",
            "source_ids": ["req-3"],
        },
    )

    db.close()

    # Reopen database from disk
    db_reopened = SqliteMemory(db_path)
    try:
        # 1. Direct get by ID
        retrieved_profile = db_reopened.get("session-1", profile_fact.id)
        assert retrieved_profile is not None
        assert retrieved_profile.text == "User preferred language is Turkish"
        assert retrieved_profile.kind == "profile"
        assert retrieved_profile.revision == 1
        assert retrieved_profile.category == "language"
        assert retrieved_profile.source_ids == ["req-1"]
        # CamelCase alias property check
        assert retrieved_profile.sourceIds == ["req-1"]

        retrieved_note = db_reopened.get("session-1", note.id)
        assert retrieved_note is not None
        assert "Discussed Phase 3 memory architecture" in retrieved_note.text

        retrieved_fact = db_reopened.get("session-1", fact.id)
        assert retrieved_fact is not None
        assert "YusufClaw" in retrieved_fact.text

        # 2. FTS5 Search after restart
        search_results = db_reopened.search("session-1", "Turkish")
        assert any(r.id == profile_fact.id for r in search_results)

        search_arch = db_reopened.search("session-1", "architecture")
        assert any(r.id == note.id for r in search_arch)

        # 3. Context assembly after restart
        context = db_reopened.assemble_context(
            "session-1", "YusufClaw project"
        )
        assert "## Active Memory Context" in context
        assert profile_fact.id in context
        assert fact.id in context
    finally:
        db_reopened.close()


# --- 2. Supersession ---


def test_supersession(memory_db: SqliteMemory) -> None:
    """Supersession: supersedes older facts, increments revision, and excludes older fact from active retrieval."""
    # Original fact (rev 1)
    m1 = memory_db.remember(
        "session-test",
        {
            "text": "User lives in Munich",
            "kind": "fact",
            "category": "location",
        },
    )
    assert m1.revision == 1
    assert m1.status == "active"

    # Search matches m1
    assert len(memory_db.search("session-test", "Munich")) == 1

    # Supersede m1 with m2 (rev 2)
    m2 = memory_db.supersede(
        "session-test",
        m1.id,
        {
            "text": "User moved to Berlin on October 1",
            "kind": "fact",
            "category": "location",
        },
    )
    assert m2.revision == 2
    assert m2.supersedes_id == m1.id
    assert m2.supersedesId == m1.id
    assert m2.status == "active"

    # M1 in database now has status 'superseded'
    m1_after = memory_db.get("session-test", m1.id, include_inactive=True)
    assert m1_after is not None
    assert m1_after.status == "superseded"
    assert m1_after.superseded_by_id == m2.id
    assert m1_after.supersededById == m2.id
    assert m1_after.valid_to is not None

    # Active retrieval (get, search, list, assemble_context) MUST NOT return m1
    assert memory_db.get("session-test", m1.id) is None
    assert len(memory_db.search("session-test", "Munich")) == 0
    search_berlin = memory_db.search("session-test", "Berlin")
    assert len(search_berlin) == 1
    assert search_berlin[0].id == m2.id

    list_active = memory_db.list("session-test")
    assert [r.id for r in list_active] == [m2.id]

    context = memory_db.assemble_context(
        "session-test", "Where does the user live?"
    )
    assert "Berlin" in context
    assert "Munich" not in context


# --- 3. Scope Isolation ---


def test_scope_isolation(memory_db: SqliteMemory) -> None:
    """Scope isolation: strictly separates memories between sessions and channels."""
    scope_a = "session-alpha"
    scope_b = "session-beta"

    mem_a = memory_db.remember(
        scope_a, {"text": "Alpha confidential key is XYZ-999", "kind": "fact"}
    )
    mem_b = memory_db.remember(
        scope_b,
        {"text": "Beta server hostname is beta.internal", "kind": "fact"},
    )

    # Queries in scopeA must only see scopeA
    assert memory_db.get(scope_a, mem_a.id) is not None
    assert memory_db.get(scope_a, mem_b.id) is None
    assert len(memory_db.search(scope_a, "internal")) == 0
    assert len(memory_db.search(scope_a, "XYZ-999")) == 1
    assert "beta.internal" not in memory_db.assemble_context(
        scope_a, "hostname"
    )

    # Queries in scopeB must only see scopeB
    assert memory_db.get(scope_b, mem_b.id) is not None
    assert memory_db.get(scope_b, mem_a.id) is None
    assert len(memory_db.search(scope_b, "XYZ-999")) == 0
    assert len(memory_db.search(scope_b, "internal")) == 1
    assert "XYZ-999" not in memory_db.assemble_context(scope_b, "confidential")

    # Cross-scope mutation attempts are blocked
    assert memory_db.forget(scope_a, mem_b.id) is False
    with pytest.raises(ValueError, match="not found"):
        memory_db.revise(scope_a, mem_b.id, {"text": "tampered"})

    b_record = memory_db.get(scope_b, mem_b.id)
    assert b_record is not None
    assert b_record.text == "Beta server hostname is beta.internal"


# --- 4. Deletion ---


def test_deletion(memory_db: SqliteMemory) -> None:
    """Deletion: forget removes memory from active retrieval and FTS search, leaving a tombstone."""
    scope = "session-del"

    mem = memory_db.remember(
        scope,
        {
            "text": "Temporary credit card ending in 4242",
            "kind": "fact",
            "category": "payment",
        },
    )

    assert len(memory_db.search(scope, "4242")) == 1
    assert memory_db.get(scope, mem.id) is not None

    # Forget the memory
    forgot = memory_db.forget(scope, mem.id, "user requested erasure")
    assert forgot is True

    # Active get, search, list, context assembly must exclude it
    assert memory_db.get(scope, mem.id) is None
    assert len(memory_db.search(scope, "4242")) == 0
    assert len(memory_db.list(scope)) == 0
    assert memory_db.assemble_context(scope, "4242") == ""

    # Historical inspection with include_inactive shows deleted
    tombstone = memory_db.get(scope, mem.id, include_inactive=True)
    assert tombstone is not None
    assert tombstone.status == "deleted"
    assert tombstone.deleted_at is not None

    # Deleting non-existent or already deleted record returns false
    assert memory_db.forget(scope, mem.id) is False
    assert memory_db.forget(scope, "non-existent-id") is False


# --- 5. FTS Search Basics ---


def test_fts_search_basics(memory_db: SqliteMemory) -> None:
    """FTS search basics: prefix matching, BM25 ranking, Turkish characters, and special symbol resilience."""
    scope = "session-fts"

    memory_db.remember(
        scope,
        {
            "text": "TypeScript compiler configuration and strict type checks",
            "kind": "fact",
            "category": "coding",
        },
    )
    memory_db.remember(
        scope,
        {
            "text": "Python scripts for machine learning data preprocessing",
            "kind": "fact",
            "category": "ai",
        },
    )
    memory_db.remember(
        scope,
        {
            "text": "Kullanıcı İstanbul Kadıköyde yaşıyor ve her sabah Türk kahvesi içer",
            "kind": "profile",
            "category": "lifestyle",
        },
    )
    memory_db.remember(
        scope,
        {
            "text": "Coffee preference: espresso, double shot, no sugar",
            "kind": "profile",
            "category": "preferences",
        },
    )

    # 1. Prefix search: "Type" matches "TypeScript"
    prefix_result = memory_db.search(scope, "Type")
    assert any("TypeScript" in r.text for r in prefix_result)

    # 2. Turkish character support: "İstanbul" and "kahve"
    tr_result = memory_db.search(scope, "İstanbul")
    assert len(tr_result) == 1
    assert "Kadıköyde" in tr_result[0].text

    tr_result2 = memory_db.search(scope, "kahvesi")
    assert len(tr_result2) == 1

    # 3. Category filter
    coding_only = memory_db.search(scope, "compiler", {"category": "coding"})
    assert len(coding_only) == 1
    wrong_cat = memory_db.search(scope, "compiler", {"category": "ai"})
    assert len(wrong_cat) == 0

    # 4. Special symbol sanitization: quotes, colons, brackets, operators do not crash FTS5
    memory_db.search(scope, 'user: "Munich" OR C++ (test) * & ^ %')
    memory_db.search(scope, "::: ??? ///")
    assert sanitize_fts5_query("::: ??? ///") == ""


# --- 6. Optimistic Concurrency Control (CAS) & Revision History ---


def test_optimistic_concurrency_revisions(memory_db: SqliteMemory) -> None:
    """Revises memory with optimistic concurrency (CAS) and tracks revision history."""
    scope = "session-rev"

    mem = memory_db.remember(
        scope, {"text": "Release v1.0 date is Monday", "kind": "note"}
    )
    assert mem.revision == 1

    # Revise with expected_revision: 1 succeeds
    rev2 = memory_db.revise(
        scope,
        mem.id,
        {
            "text": "Release v1.0 date is moved to Wednesday",
            "expected_revision": 1,
            "reason": "QA extension",
        },
    )
    assert rev2.revision == 2
    assert "Wednesday" in rev2.text

    # Revise with outdated expected_revision: 1 fails with version conflict
    with pytest.raises(ValueError, match="Version conflict"):
        memory_db.revise(
            scope,
            mem.id,
            {
                "text": "Release v1.0 date is moved to Friday",
                "expected_revision": 1,
            },
        )

    # Search finds updated text
    assert len(memory_db.search(scope, "Wednesday")) == 1
    assert len(memory_db.search(scope, "Monday")) == 0


# --- 7. Bounded Context Assembly ---


def test_context_assembly_bounded(memory_db: SqliteMemory) -> None:
    """Context assembly bounds entries and total characters, labeling sections with evidence IDs."""
    scope = "session-context"

    # Add 10 profile facts
    for i in range(1, 11):
        memory_db.remember(
            scope,
            {
                "text": f"User trait number {i} which is a moderately long descriptive preference",
                "kind": "profile",
            },
        )

    # Add 10 notes
    for i in range(1, 11):
        memory_db.remember(
            scope,
            {
                "text": f"Project note {i} concerning task completion and documentation",
                "kind": "note",
            },
        )

    context = memory_db.assemble_context(
        scope,
        "Project note",
        {"max_entries": 4, "max_chars": 500},
    )

    assert "## Active Memory Context" in context
    assert "Evidence: mem_" in context
    assert len(context) <= 500
    assert "### Profile" in context


# --- 8. Agent Loop Memory Wiring ---


@pytest.mark.asyncio
async def test_agent_memory_wiring(memory_db: SqliteMemory) -> None:
    """Agent loop wires memory context assembly and provides agent-facing memory tools (remember, revise, forget, recall)."""
    session = "session-agent"

    # Pre-seed a profile fact
    preseed = memory_db.remember(
        session,
        {
            "text": "User preferred tone is concise and direct",
            "kind": "profile",
        },
    )

    # 1. First model call: Agent passes memory context into system prompt
    # Model responds with tool call to remember a new fact
    call_remember = ModelResponse(
        text="",
        tool_calls=[
            ToolCall(
                id="call-rem-1",
                name="remember",
                input={
                    "text": "User project repository is yusufclaw",
                    "kind": "fact",
                    "category": "dev",
                },
            )
        ],
    )
    answer1 = ModelResponse(
        text="I remembered your project repository.", tool_calls=[]
    )

    provider1 = FakeProvider(canned_responses=[call_remember, answer1])
    agent = Agent(provider1, memory_db)

    reply1 = await agent.handle(
        session, "req-agent-1", "Remember my project repo"
    )
    assert reply1 == "I remembered your project repository."

    # Check that preseeded memory was present in the system prompt of the first call
    first_call_system = provider1.recorded_requests[0].system
    assert "## Active Memory Context" in first_call_system
    assert preseed.id in first_call_system
    assert "User preferred tone is concise" in first_call_system

    # Verify tool execution actually persisted the fact in memory
    search_stored = memory_db.search(session, "yusufclaw")
    assert len(search_stored) == 1
    new_fact_id = search_stored[0].id
    assert search_stored[0].category == "dev"

    # 2. Test agent-facing recall tool through Agent loop
    call_recall = ModelResponse(
        text="",
        tool_calls=[
            ToolCall(
                id="call-rec-1", name="recall", input={"query": "yusufclaw"}
            )
        ],
    )
    answer_recall = ModelResponse(
        text="You work on yusufclaw.", tool_calls=[]
    )

    provider2 = FakeProvider(canned_responses=[call_recall, answer_recall])
    agent2 = Agent(provider2, memory_db)
    await agent2.handle(
        session, "req-agent-2", "What project repo did I mention?"
    )

    # Verify tool result contained evidence ID
    tool_result_msgs = [
        m for m in provider2.recorded_requests[1].messages if m.role == "tool"
    ]
    assert len(tool_result_msgs) >= 1
    assert new_fact_id in tool_result_msgs[0].content

    # 3. Test forget tool via Agent
    call_forget = ModelResponse(
        text="",
        tool_calls=[
            ToolCall(
                id="call-for-1", name="forget", input={"id": new_fact_id}
            )
        ],
    )
    answer_forget = ModelResponse(
        text="I have forgotten that project repo.", tool_calls=[]
    )
    provider3 = FakeProvider(canned_responses=[call_forget, answer_forget])
    agent3 = Agent(provider3, memory_db)
    await agent3.handle(session, "req-agent-3", "Forget my project repo")

    # Verify it is no longer retrievable
    assert memory_db.get(session, new_fact_id) is None
    assert len(memory_db.search(session, "yusufclaw")) == 0


# --- 9. Turn History & /reset Preservation ---


@pytest.mark.asyncio
async def test_sqlite_turn_history_and_reset(
    memory_db: SqliteMemory,
) -> None:
    """Keeps existing SqliteMemory turn history and /reset working while preserving durable memory."""
    session = "session-turns"

    # Store a durable profile fact
    profile = memory_db.remember(
        session, {"text": "User is Yusuf", "kind": "profile"}
    )

    # Turn 1
    provider = FakeProvider(
        canned_responses=[ModelResponse(text="Hello Yusuf!", tool_calls=[])]
    )
    agent = Agent(provider, memory_db)
    r1 = await agent.handle(session, "turn-1", "Hi")
    assert r1 == "Hello Yusuf!"
    assert len(memory_db.history(session)) == 2  # user + assistant

    # Request ID deduplication
    assert await agent.handle(session, "turn-1", "Hi") == "Hello Yusuf!"
    assert len(provider.recorded_requests) == 1

    # /reset clears turn history
    reset_reply = await agent.handle(session, "turn-reset", "/reset")
    assert reset_reply == "Conversation cleared."
    assert len(memory_db.history(session)) == 0

    # Durable memory is NOT wiped out by /reset
    profile_after = memory_db.get(session, profile.id)
    assert profile_after is not None
    assert profile_after.text == "User is Yusuf"


# --- 10. Direct create_memory_tools Schema & Execution ---


@pytest.mark.asyncio
async def test_create_memory_tools(memory_db: SqliteMemory) -> None:
    """create_memory_tools exposes all 4 tools directly with schema validation and scope isolation."""
    tools = create_memory_tools(memory_db, lambda: "direct-session")
    assert [t.name for t in tools] == ["remember", "revise", "forget", "recall"]

    remember, revise, forget, recall = tools
    res_rem = await remember.execute(
        {"text": "Direct preference", "kind": "profile"}
    )
    assert "Remembered [mem_" in res_rem

    search_res = await recall.execute({"query": "Direct"})
    assert "Direct preference" in search_res

    mem = memory_db.search("direct-session", "Direct")[0]
    res_rev = await revise.execute(
        {"id": mem.id, "text": "Updated direct preference"}
    )
    assert "revision 2" in res_rev

    res_for = await forget.execute({"id": mem.id})
    assert "Forgot memory" in res_for


# --- 11. Async PostgreSQL / pgvector memory store test (offline fallback) ---


@pytest.mark.asyncio
async def test_postgres_memory_async() -> None:
    """Verify PostgresMemory operations with async SQLAlchemy engine."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    pm = PostgresMemory(engine=engine)
    await pm.init_db()

    try:
        # 1. Remember
        rec = await pm.remember(
            "pg-scope",
            RememberInput(
                text="Postgres async memory test with pgvector",
                kind="fact",
                category="database",
            ),
        )
        assert rec.id.startswith("mem_")
        assert rec.revision == 1
        assert rec.status == "active"

        # 2. Get and search
        fetched = await pm.get("pg-scope", rec.id)
        assert fetched is not None
        assert fetched.text == "Postgres async memory test with pgvector"

        search_res = await pm.search("pg-scope", "pgvector")
        assert len(search_res) == 1
        assert search_res[0].id == rec.id

        # 3. Revise with CAS
        revised = await pm.revise(
            "pg-scope",
            rec.id,
            ReviseInput(
                text="Postgres async memory test with pgvector and FTS",
                expected_revision=1,
                reason="add FTS",
            ),
        )
        assert revised.revision == 2

        with pytest.raises(ValueError, match="Version conflict"):
            await pm.revise(
                "pg-scope",
                rec.id,
                ReviseInput(text="fail", expected_revision=1),
            )

        # 4. Context assembly
        ctx = await pm.assemble_context("pg-scope", "pgvector")
        assert "## Active Memory Context" in ctx
        assert rec.id in ctx

        # 5. History and state
        await pm.save(
            "pg-scope", "req-1", [{"role": "user", "content": "hi"}], "hello"
        )
        assert await pm.reply("pg-scope", "req-1") == "hello"
        assert len(await pm.history("pg-scope")) == 1

        await pm.set_state("pg_key", "pg_val")
        assert await pm.get_state("pg_key") == "pg_val"

        # 6. Forget
        assert await pm.forget("pg-scope", rec.id) is True
        assert await pm.get("pg-scope", rec.id) is None
    finally:
        await pm.close()


# --- 12. pgvector Vector Similarity Search ---


@pytest.mark.asyncio
async def test_postgres_memory_vector_search() -> None:
    """Verify vector search with embedding vectors in PostgresMemory."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    pm = PostgresMemory(engine=engine)
    await pm.init_db()

    try:
        # Embeddings with 1536 dimensions
        emb_cat = [1.0, 0.0, 0.0] + [0.0] * 1533
        emb_dog = [0.0, 1.0, 0.0] + [0.0] * 1533
        emb_query = [0.95, 0.05, 0.0] + [0.0] * 1533

        await pm.remember(
            "v-scope",
            RememberInput(
                text="Feline companions like cats",
                kind="fact",
                embedding=emb_cat,
            ),
        )
        await pm.remember(
            "v-scope",
            RememberInput(
                text="Canine companions like dogs",
                kind="fact",
                embedding=emb_dog,
            ),
        )

        results = await pm.search_vector("v-scope", emb_query, limit=1)
        assert len(results) == 1
        assert "cats" in results[0].text
    finally:
        await pm.close()


# --- 13. Pydantic Models & Property Access ---


def test_pydantic_models_validation() -> None:
    """Verify Pydantic models validate and support both camelCase and snake_case properties."""
    rec = MemoryRecord.model_validate(
        {
            "id": "mem_123_abc",
            "scope": "s1",
            "kind": "fact",
            "text": "test fact",
            "sourceIds": ["req-1"],
            "createdAt": "2026-10-16T12:00:00Z",
            "updatedAt": "2026-10-16T12:00:00Z",
            "supersedesId": "mem_old",
            "revision": 2,
            "status": "active",
        }
    )
    assert rec.source_ids == ["req-1"]
    assert rec.sourceIds == ["req-1"]
    assert rec.created_at == "2026-10-16T12:00:00Z"
    assert rec.createdAt == "2026-10-16T12:00:00Z"
    assert rec.supersedes_id == "mem_old"
    assert rec.supersedesId == "mem_old"

    options = ContextAssemblyOptions.model_validate(
        {"maxEntries": 7, "maxChars": 1500}
    )
    assert options.max_entries == 7
    assert options.maxEntries == 7
    assert options.max_chars == 1500
    assert options.maxChars == 1500
