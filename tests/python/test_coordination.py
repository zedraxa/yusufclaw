import asyncio
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from yusufclaw.coordination import (
    ArtifactInput,
    CoordinationStore,
    Evidence,
    PublishInput,
    SqliteCoordination,
    Verification,
)


async def test_publish_search_visit_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "coord.sqlite"
    store: CoordinationStore = SqliteCoordination(path)
    artifact = await store.put_artifact(
        ArtifactInput(
            scope="team",
            actor="researcher",
            name="measurements",
            description="temperature observations",
        ),
        b"21 C",
    )
    event = await store.publish(
        PublishInput(
            scope="team",
            actor="researcher",
            text="Experiment complete",
            kind="artifact_ref",
            artifact_ids=(artifact.id,),
        )
    )
    assert (await store.search("experiment", scope="team"))[0].id == event.id
    assert (await store.search("temperature", scope="team"))[0].id == artifact.id
    await store.close()
    store = SqliteCoordination(path)
    visit = await store.visit(event.id, scope="team")
    assert visit.evidence.event == event
    assert visit.evidence.blobs == (b"21 C",)
    assert visit.evidence.artifacts == (artifact,)
    assert not visit.verified
    assert (await store.visit(artifact.id, scope="team")).evidence.blobs == (b"21 C",)
    await store.close()


async def test_append_only_and_pagination(tmp_path: Path) -> None:
    path = tmp_path / "coord.sqlite"
    store = SqliteCoordination(path)
    first = await store.publish(PublishInput(scope="s", actor="a", text="original"))
    second = await store.publish(
        PublishInput(scope="s", actor="b", kind="claim", text="correction", source_ids=(first.id,))
    )
    third = await store.publish(PublishInput(scope="s", actor="b", kind="result", text="done"))
    with sqlite3.connect(path) as conn:
        for sql in ("UPDATE events SET payload='{}'", "DELETE FROM events"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute(sql)
    assert await store.list_events(scope="s") == [first, second, third]
    assert await store.list_events(scope="s", after=first.sequence, limit=1) == [second]
    with pytest.raises(ValidationError):
        first.text = "changed"  # type: ignore[misc]
    await store.close()


async def test_ranking_and_citations_never_verify() -> None:
    store = SqliteCoordination()
    first = await store.publish(PublishInput(scope="s", actor="a", text="unproven claim"))
    for _ in range(8):
        await store.publish(
            PublishInput(scope="s", actor="b", text="unproven claim", source_ids=(first.id,))
        )
    for _ in range(3):
        hits = await store.search("unproven", scope="s")
        assert len(hits) == 9
        assert all(hit.score > 0 and not hit.verified for hit in hits)
        assert not (await store.visit(first.id, scope="s")).verified
    await store.close()


async def test_verifier_checks_bytes_and_verdict_is_not_persisted() -> None:
    class CheckEvidence:
        async def verify(self, evidence: Evidence) -> Verification:
            assert evidence.blobs == (b"42",)
            return Verification(
                status="verified",
                reason="Checked measurement bytes",
                checked_ids=(evidence.artifacts[0].id,),
            )

    store = SqliteCoordination()
    artifact = await store.put_artifact(ArtifactInput(scope="s", actor="a", name="answer"), b"42")
    assert (await store.visit(artifact.id, scope="s", verifier=CheckEvidence())).verified
    assert not (await store.visit(artifact.id, scope="s")).verified
    assert not (await store.search("answer", scope="s"))[0].verified
    await store.close()


async def test_verifier_failure_and_invalid_evidence_fail_closed() -> None:
    class Broken:
        async def verify(self, evidence: Evidence) -> Verification:
            raise RuntimeError("offline")

    class Unsupported:
        async def verify(self, evidence: Evidence) -> Verification:
            return Verification(status="verified", reason="unsupported", checked_ids=("missing",))

    store = SqliteCoordination()
    event = await store.publish(PublishInput(scope="s", actor="a", text="claim"))
    with pytest.raises(RuntimeError, match="offline"):
        await store.visit(event.id, scope="s", verifier=Broken())
    with pytest.raises(ValueError, match="outside"):
        await store.visit(event.id, scope="s", verifier=Unsupported())
    with pytest.raises(ValidationError):
        Verification(status="verified")
    await store.close()


async def test_scope_isolation_and_failed_publish_is_atomic() -> None:
    store = SqliteCoordination()
    artifact = await store.put_artifact(
        ArtifactInput(scope="private", actor="a", name="secret"), b"x"
    )
    event = await store.publish(PublishInput(scope="private", actor="a", text="secret"))
    for ref in (artifact.id, event.id, "missing"):
        with pytest.raises(KeyError):
            await store.visit(ref, scope="other")
    for kwargs in ({"artifact_ids": (artifact.id,)}, {"source_ids": (event.id,)}):
        with pytest.raises(KeyError):
            await store.publish(PublishInput(scope="other", actor="b", text="secret", **kwargs))
    assert await store.search("secret", scope="other") == []
    assert await store.list_events(scope="other") == []
    assert await store.search('" * ()', scope="private") == []
    assert await store.search('secret OR "', scope="private")
    with pytest.raises(ValueError):
        await store.search("secret", scope="private", limit=0)
    await store.close()


async def test_concurrent_connections(tmp_path: Path) -> None:
    path = tmp_path / "coord.sqlite"
    stores = [SqliteCoordination(path), SqliteCoordination(path)]
    events = await asyncio.gather(
        *(
            stores[i % 2].publish(PublishInput(scope="s", actor="a", text=f"event {i}"))
            for i in range(20)
        )
    )
    assert len({event.sequence for event in events}) == 20
    assert len(await stores[0].list_events(scope="s")) == 20
    assert len(await stores[1].search("event", scope="s")) == 20
    for store in stores:
        await store.close()


async def test_artifacts_are_immutable(tmp_path: Path) -> None:
    path = tmp_path / "coord.sqlite"
    store = SqliteCoordination(path)
    await store.put_artifact(ArtifactInput(scope="s", actor="a", name="data"), b"data")
    with sqlite3.connect(path) as conn:
        for sql in ("UPDATE artifacts SET content=x'00'", "DELETE FROM artifacts"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                conn.execute(sql)
    await store.close()
