"""Backend boundary for SQLite now and Postgres later."""

from typing import Protocol

from .models import (
    Artifact,
    ArtifactInput,
    Event,
    Evidence,
    PublishInput,
    SearchHit,
    Verification,
    Visit,
)


class Verifier(Protocol):
    async def verify(self, evidence: Evidence) -> Verification:
        """Check actual evidence; ranking, citations and popularity are not proof."""
        ...


class CoordinationStore(Protocol):
    async def publish(self, item: PublishInput) -> Event: ...
    async def put_artifact(self, item: ArtifactInput, content: bytes) -> Artifact: ...
    async def search(self, query: str, *, scope: str, limit: int = 20) -> list[SearchHit]: ...
    async def visit(self, ref: str, *, scope: str, verifier: Verifier | None = None) -> Visit: ...
    async def list_events(self, *, scope: str, after: int = 0, limit: int = 100) -> list[Event]: ...
    async def close(self) -> None: ...
