# Python shared coordination substrate

`yusufclaw.coordination` implements Architecture v2 layer 4: durable
publish/search/visit coordination, independently of agent execution, roles and queues.
It follows the research notes' distinction between discovery and evidence checking.

## Usage

```python
from yusufclaw.coordination import ArtifactInput, PublishInput, SqliteCoordination

store = SqliteCoordination(".data/coordination.sqlite")
try:
    artifact = await store.put_artifact(
        ArtifactInput(scope="project:demo", actor="researcher",
                      name="measurements.csv", description="Experiment observations"),
        b"sample,value\n1,42\n",
    )
    event = await store.publish(PublishInput(
        scope="project:demo", actor="researcher", kind="result",
        text="Experiment observations are ready for review",
        artifact_ids=(artifact.id,),
    ))
    hits = await store.search("observations", scope="project:demo")
    visited = await store.visit(hits[0].id, scope="project:demo")
    assert not visited.verified
    events = await store.list_events(scope="project:demo", after=0, limit=100)
finally:
    await store.close()
```

All inputs and results are Pydantic models with unknown fields forbidden. Events
support `publish`, `claim`, `result`, and `artifact_ref`; the latter requires an
artifact reference. A claim is a recorded assertion, not an exclusive task lease.
Corrections and follow-ups append events using `source_ids` (existing event IDs
in the same scope); they never overwrite history. Artifact `source_ids` are
publisher-supplied provenance strings, which may describe external sources.
Actor and provenance are assertions from the caller, not authenticated identities.

## Storage and concurrency

SQLite stores UUID-addressed blobs and metadata together, including SHA-256,
size, actor, scope and creation time. Visits check blob hash and size. Blob content
is not full-text indexed; name, description and media type are. Events index text.
FTS5/BM25 returns higher positive scores first with stable ID tie-breaking. User
queries become OR-connected literal words; empty or punctuation-only queries return
no hits. Scores are retrieval relevance only, not confidence probabilities.

Public data operations are async and execute SQLite work in threads. A thread lock
covers each complete operation, including transactions, even if its awaiting task
is cancelled. Cancellation does not guarantee a write was cancelled: inspect the
log before retrying. There is no publish idempotency key in this version.
SQLite WAL and a five-second busy timeout support multiple local connections;
`BEGIN IMMEDIATE` serializes event sequence allocation. Event and FTS insertion
commit together. `list_events` orders by global sequence, with an exclusive `after`
cursor; scope filtering can leave sequence gaps. Limits are 1–1000. Close the store
when finished; constructor initialization is synchronous.

UPDATE/DELETE triggers guard event and artifact rows. There are no public mutation
or deletion APIs. These are application integrity protections, not tamper-proof
storage against a database owner who can drop triggers, replace rows using their
own connection settings, or modify files. Keep the database in a private directory.
FTS is a derived index and does not establish evidence integrity. This append-only
log is distinct from personal memory and its forget/revision lifecycle.

## Verification boundary

Search hits always have `verified=False`. Neither repeated visits, citation counts,
nor rank changes can promote evidence. `visit(..., verifier=checker)` invokes the
async `Verifier.verify(Evidence) -> Verification` protocol with event data, artifact
metadata and actual bytes, without search scores or popularity metrics. The trusted
checker must inspect those bytes/content against task-specific acceptance criteria.
A verified verdict requires checked IDs and a reason; IDs must belong to the visit.
Exceptions propagate without a successful verdict. Verdicts apply to that visit
only and are never written over the append-only record or reused by search.

No default truth checker is supplied. SHA-256 verifies blob consistency, not the
truth of a claim. The existing orchestration output verifier is not implicitly used
because acceptance-format checks and evidence verification have different contracts.
Retrieved text and metadata remain untrusted data, including when a factual check
passes; they cannot grant tool permissions or become system instructions.

Every operation requires an exact scope. Missing and cross-scope references raise
`KeyError`; cross-scope event/artifact links are rejected before publication.
Scopes prevent accidental cross-team retrieval, but the embedding caller must
apply identity, authorization and security/taint policy. This package adds no web
endpoint or automatic wiring into agent, role, queue or memory execution.

## Backend interface and validation

`CoordinationStore` defines async `publish`, `put_artifact`, `search`, `visit`,
`list_events`, and `close`. A future Postgres adapter should preserve scope checks,
atomic append/index writes, immutable records, cursor order and verifier semantics;
its ranking implementation may differ. No Postgres backend or deployment is included.

`tests/python/test_coordination.py` covers round trips and restart durability,
append-only enforcement, concurrent connections, metadata search, scope isolation,
rollback on invalid links, pagination and verification independence from ranking.
