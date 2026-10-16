"""Offline acceptance tests for memory lifecycle, ranking and compaction."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from yusufclaw.memory.compaction import (
    CompactionOptions,
    ContextCompactor,
    LLMSummarizer,
)
from yusufclaw.memory.consolidation import ConsolidationJob, ConsolidationOptions, similarity
from yusufclaw.memory.context import memory_score
from yusufclaw.memory.models import ContextAssemblyOptions, MemoryRecord, RememberInput
from yusufclaw.memory.postgres import MemoryDeletionModel, MemoryRevisionModel, PostgresMemory
from yusufclaw.memory.sqlite import SqliteMemory
from yusufclaw.memory.store import resolve
from yusufclaw.models.fake import FakeProvider
from yusufclaw.models.types import Message, ModelResponse, ToolCall


@pytest.fixture(params=["sqlite", "async"])
async def store(request):
    if request.param == "sqlite":
        memory = SqliteMemory()
    else:
        memory = PostgresMemory(engine=create_async_engine("sqlite+aiosqlite:///:memory:"))
        await memory.init_db()
    try:
        yield memory
    finally:
        await resolve(memory.close())


async def test_merge_audit_scope_and_idempotence(store):
    first = await resolve(
        store.remember("a", {"text": "User prefers the dark theme", "source_ids": ["r1"]})
    )
    second = await resolve(
        store.remember("a", {"text": "User prefers dark theme", "source_ids": ["r2"]})
    )
    foreign = await resolve(store.remember("b", {"text": second.text}))
    job = ConsolidationJob(store, ConsolidationOptions(similarity_threshold=0.88))
    report = await job.run("a")
    assert report.merged == [(second.id, first.id)]
    kept = await resolve(store.get("a", first.id))
    assert kept.revision == 2
    assert {"r1", "r2", second.id} <= set(kept.source_ids)
    assert kept.text == first.text
    assert (await resolve(store.get("a", second.id, True))).text == second.text
    assert await resolve(store.get("a", second.id)) is None
    assert (await resolve(store.get("a", second.id, True))).status == "deleted"
    assert await resolve(store.get("b", foreign.id)) is not None
    assert (await job.run("a")).merged == []
    assert (await resolve(store.get("a", first.id))).revision == 2
    if isinstance(store, SqliteMemory):
        assert store._conn.execute("SELECT count(*) FROM memory_revisions").fetchone()[0] == 1
        assert store._conn.execute("SELECT count(*) FROM memory_deletions").fetchone()[0] == 1
        assert (
            store._conn.execute(
                "SELECT count(*) FROM memory_fts WHERE record_id=?", (second.id,)
            ).fetchone()[0]
            == 0
        )
    else:
        async with store.session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(MemoryRevisionModel)) == 1
            assert await session.scalar(select(func.count()).select_from(MemoryDeletionModel)) == 1


async def test_promote_journal_and_do_not_resurrect(store):
    notes = [
        await resolve(
            store.remember(
                "a",
                RememberInput(
                    text="User prefers tea without sugar",
                    kind="note",
                    category="journal",
                    source_ids=[f"turn-{i}"],
                ),
            )
        )
        for i in range(2)
    ]
    job = ConsolidationJob(store)
    report = await job.run("a")
    assert len(report.promoted) == 1
    fact = await resolve(store.get("a", report.promoted[0]))
    assert fact.kind == "fact" and fact.trust == "user_explicit"
    assert set(n.id for n in notes) <= set(fact.source_ids)
    assert (await resolve(store.get("a", notes[0].id, True))).status == "superseded"
    assert (await job.run("a")).promoted == []
    await resolve(store.forget("a", fact.id, "user erasure"))
    assert (await job.run("a")).promoted == []
    assert await resolve(store.list("a")) == []


async def test_promotion_requires_independent_user_episodes(store):
    for trust in ("user_explicit", "external"):
        for _ in range(2):
            await resolve(
                store.remember(
                    "a",
                    dict(
                        text="Repeated claim",
                        kind="note",
                        category="journal",
                        trust=trust,
                        source_ids=["same-event"],
                    ),
                )
            )
    assert (await ConsolidationJob(store).run("a")).promoted == []
    assert len(await resolve(store.list("a"))) == 4


async def test_threshold_pin_and_trust_boundaries(store):
    a = await resolve(store.remember("a", {"text": "User prefers the dark theme"}))
    b = await resolve(store.remember("a", {"text": "User prefers dark theme"}))
    await resolve(store.remember("a", {"text": b.text, "trust": "external"}))
    assert not (
        await ConsolidationJob(store, ConsolidationOptions(similarity_threshold=1)).run("a")
    ).merged
    assert not (
        await ConsolidationJob(
            store, ConsolidationOptions(similarity_threshold=0.8, pinned_ids={a.id})
        ).run("a")
    ).merged
    assert len(await resolve(store.list("a"))) == 3


@pytest.mark.parametrize(
    "a,b",
    [
        ("User likes coffee", "User does not like coffee"),
        ("Budget is 100", "Budget is 1000"),
        ("User lives in Ankara", "User lives in Antalya"),
        ("A follows B", "B follows A"),
    ],
)
def test_conflicts_are_not_duplicates(a, b):
    assert similarity(a, b) == 0


async def test_interrupted_merge_finishes_retirement(store):
    a = await resolve(store.remember("a", {"text": "Same fact"}))
    b = await resolve(store.remember("a", {"text": "Same fact"}))
    await resolve(
        store.revise("a", a.id, {"text": a.text, "source_ids": [f"consolidation:{b.id}"]})
    )
    await ConsolidationJob(store).run("a")
    assert await resolve(store.get("a", b.id)) is None
    assert (await resolve(store.get("a", a.id))).revision == 2


async def test_ranking_in_actual_assembly(store):
    a = await resolve(store.remember("a", {"text": "Project alpha important", "kind": "fact"}))
    b = await resolve(store.remember("a", {"text": "Project alpha other", "kind": "fact"}))
    context = await resolve(
        store.assemble_context(
            "a",
            "Project alpha",
            ContextAssemblyOptions(max_entries=1, max_search_entries=1, importance={a.id: 100}),
        )
    )
    assert a.id in context and b.id not in context
    assert len(await resolve(store.assemble_context("a", "Project", {"max_chars": 1}))) <= 1


def test_decay_pin_expiry_and_importance():
    now = datetime(2026, 10, 16, tzinfo=UTC)
    record = MemoryRecord(
        id="x",
        scope="a",
        kind="fact",
        text="x",
        created_at=now.isoformat(),
        updated_at=(now - timedelta(days=90)).isoformat(),
    )
    assert memory_score(record, now=now) == 0.5
    assert memory_score(record, now=now, importance=2) == 1
    assert memory_score(record, now=now, pinned=True) == 1
    expired = record.model_copy(update={"valid_to": (now - timedelta(days=1)).isoformat()})
    assert memory_score(expired, now=now, pinned=True) < 0
    with pytest.raises(ValueError):
        memory_score(record, importance=float("nan"))


def conversation():
    return [
        Message("user", "Old request " * 200),
        Message("assistant", "", tool_calls=[ToolCall("call-1", "read", {"path": "report.txt"})]),
        Message("tool", "Result data " * 300, tool_call_id="call-1"),
        Message("assistant", "Decision: ship on Monday. " * 100),
        Message("user", "What next?"),
        Message("assistant", "Run tests."),
    ]


async def test_compaction_preserves_protected_state_and_history():
    messages = conversation()
    compactor = ContextCompactor(
        CompactionOptions(token_budget=500, summary_tokens=120, keep_recent_turns=1)
    )
    result = await compactor.compact(
        messages,
        pinned_facts=["User is Yusuf"],
        open_tasks=["Check release"],
        tool_references=["artifact:report.txt"],
    )
    assert result.compacted_messages == 4
    assert result.messages == messages[-2:]
    assert result.summary and result.estimated_tokens <= 500 and not result.over_budget
    assert result.tool_references == ["artifact:report.txt", "call-1"]
    assert "User is Yusuf" in result.context and "Check release" in result.context
    assert len(messages) == 6 and len(messages[2].content) > 1000
    again = await compactor.compact(
        messages,
        pinned_facts=["User is Yusuf"],
        open_tasks=["Check release"],
        tool_references=["artifact:report.txt"],
    )
    assert result == again


async def test_no_compaction_below_budget():
    provider = FakeProvider()
    result = await ContextCompactor(summarizer=LLMSummarizer(provider)).compact(
        [Message("user", "hi")]
    )
    assert result.compacted_messages == 0 and result.summary == ""
    assert not provider.recorded_requests


async def test_pluggable_llm_and_empty_response_fallback():
    provider = FakeProvider(
        canned_responses=[ModelResponse(text="A decision was made."), ModelResponse(text="")]
    )
    compactor = ContextCompactor(
        CompactionOptions(token_budget=500, keep_recent_turns=1), LLMSummarizer(provider)
    )
    result = await compactor.compact(conversation())
    assert result.summary == "A decision was made."
    assert provider.recorded_requests[0].tools == []
    assert provider.recorded_requests[0].max_tokens <= 512
    fallback = await compactor.compact(conversation())
    assert fallback.used_fallback and fallback.summary


async def test_unfinished_tool_and_protected_overflow():
    messages = [
        Message("user", "x" * 1000),
        Message("assistant", "", tool_calls=[ToolCall("pending", "read", {})]),
        Message("user", "next"),
    ]
    result = await ContextCompactor(
        CompactionOptions(token_budget=30, keep_recent_turns=1)
    ).compact(messages, pinned_facts=["pin" * 100], open_tasks=["task" * 100])
    assert result.messages == messages and result.compacted_messages == 0
    assert result.over_budget and result.pinned_facts == ["pin" * 100]


async def test_unicode_and_tool_pair_boundary():
    messages = conversation()
    messages[0].content = "İstanbul 東京 " * 100
    messages.insert(-1, Message("tool", "late", tool_call_id="call-1"))
    result = await ContextCompactor(
        CompactionOptions(token_budget=100, keep_recent_turns=1)
    ).compact(messages)
    assert result.compacted_messages == 0  # Cannot cut before the late paired result.
    assert result.over_budget


async def test_summarizer_exception_and_oversized_output():
    class Broken:
        async def summarize(self, messages, max_tokens):
            raise RuntimeError("offline")

    options = CompactionOptions(token_budget=300, keep_recent_turns=1)
    result = await ContextCompactor(options, Broken()).compact(conversation())
    assert result.used_fallback and result.summary and result.estimated_tokens <= 300
    provider = FakeProvider(default_text="東京" * 2000)
    result = await ContextCompactor(options, LLMSummarizer(provider)).compact(conversation())
    assert result.summary and result.estimated_tokens <= 300


async def test_no_summary_space_retains_history():
    messages = conversation()
    result = await ContextCompactor(CompactionOptions(token_budget=1, keep_recent_turns=0)).compact(
        messages
    )
    assert result.messages == messages
    assert result.compacted_messages == 0 and result.over_budget


async def test_bounded_job_and_disabled_promotion(store):
    for i in range(3):
        await resolve(
            store.remember(
                "a",
                dict(
                    text="Same journal claim", kind="note", category="journal", source_ids=[str(i)]
                ),
            )
        )
    result = await ConsolidationJob(store, ConsolidationOptions(max_records=2, promote=False)).run(
        "a"
    )
    assert result.at_capacity and result.scanned == 2 and not result.promoted
    assert len(await resolve(store.list("a"))) == 3


def test_invalid_options_and_signed_numbers():
    with pytest.raises(ValueError):
        CompactionOptions(token_budget=0)
    with pytest.raises(ValueError):
        ConsolidationOptions(similarity_threshold=1.1)
    assert similarity("Balance is -100", "Balance is 100") == 0
