"""Coordination data is evidence to inspect, never an instruction or authority."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

EventKind = Literal["publish", "claim", "result", "artifact_ref"]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PublishInput(Record):
    scope: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    kind: EventKind = "publish"
    text: str = Field(min_length=1)
    artifact_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_artifact(self) -> Self:
        if self.kind == "artifact_ref" and not self.artifact_ids:
            raise ValueError("artifact_ref requires an artifact")
        return self


class Event(PublishInput):
    id: str
    sequence: int
    created_at: datetime


class ArtifactInput(Record):
    scope: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    media_type: str = "application/octet-stream"
    source_ids: tuple[str, ...] = ()


class Artifact(ArtifactInput):
    id: str
    sha256: str
    size: int
    created_at: datetime


class Evidence(Record):
    event: Event | None = None
    artifacts: tuple[Artifact, ...] = ()
    blobs: tuple[bytes, ...] = ()


class Verification(Record):
    status: Literal["unverified", "verified", "rejected"] = "unverified"
    reason: str = "Evidence has not been checked"
    checked_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_evidence(self) -> Self:
        if self.status == "verified" and (not self.checked_ids or not self.reason.strip()):
            raise ValueError("verification requires checked evidence IDs and a reason")
        return self


class Visit(Record):
    evidence: Evidence
    verification: Verification = Field(default_factory=Verification)

    @property
    def verified(self) -> bool:
        return self.verification.status == "verified"


class SearchHit(Record):
    id: str
    kind: Literal["event", "artifact"]
    text: str
    score: float
    verified: Literal[False] = False
