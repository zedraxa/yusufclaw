"""Pydantic models and type definitions for the YusufClaw memory subsystem."""

from collections.abc import Awaitable
from typing import Literal, Protocol, runtime_checkable

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

MemoryKind = Literal["profile", "fact", "note"]
MemoryTrust = Literal["user_explicit", "observed", "model_inferred", "external"]
MemoryStatus = Literal["active", "superseded", "deleted"]


class MemoryRecord(BaseModel):
    """Structured durable memory record with provenance and lifecycle state."""

    id: str
    scope: str
    kind: MemoryKind
    text: str
    category: str | None = None
    source: str = "user_explicit"
    source_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("source_ids", "sourceIds"),
        serialization_alias="sourceIds",
    )
    trust: MemoryTrust = "user_explicit"
    created_at: str = Field(
        ...,
        validation_alias=AliasChoices("created_at", "createdAt"),
        serialization_alias="createdAt",
    )
    updated_at: str = Field(
        ...,
        validation_alias=AliasChoices("updated_at", "updatedAt"),
        serialization_alias="updatedAt",
    )
    valid_from: str | None = Field(
        default=None,
        validation_alias=AliasChoices("valid_from", "validFrom"),
        serialization_alias="validFrom",
    )
    valid_to: str | None = Field(
        default=None,
        validation_alias=AliasChoices("valid_to", "validTo"),
        serialization_alias="validTo",
    )
    supersedes_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("supersedes_id", "supersedesId"),
        serialization_alias="supersedesId",
    )
    superseded_by_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("superseded_by_id", "supersededById"),
        serialization_alias="supersededById",
    )
    revision: int = 1
    status: MemoryStatus = "active"
    deleted_at: str | None = Field(
        default=None,
        validation_alias=AliasChoices("deleted_at", "deletedAt"),
        serialization_alias="deletedAt",
    )
    embedding: list[float] | None = None

    model_config = ConfigDict(populate_by_name=True)

    @property
    def sourceIds(self) -> list[str]:
        return self.source_ids

    @property
    def createdAt(self) -> str:
        return self.created_at

    @property
    def updatedAt(self) -> str:
        return self.updated_at

    @property
    def validFrom(self) -> str | None:
        return self.valid_from

    @property
    def validTo(self) -> str | None:
        return self.valid_to

    @property
    def supersedesId(self) -> str | None:
        return self.supersedes_id

    @property
    def supersededById(self) -> str | None:
        return self.superseded_by_id

    @property
    def deletedAt(self) -> str | None:
        return self.deleted_at


class RememberInput(BaseModel):
    """Input payload to store or supersede a durable memory record."""

    text: str
    kind: MemoryKind = "fact"
    category: str | None = None
    source: str = "user_explicit"
    source_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("source_ids", "sourceIds"),
        serialization_alias="sourceIds",
    )
    trust: MemoryTrust = "user_explicit"
    valid_from: str | None = Field(
        default=None,
        validation_alias=AliasChoices("valid_from", "validFrom"),
        serialization_alias="validFrom",
    )
    valid_to: str | None = Field(
        default=None,
        validation_alias=AliasChoices("valid_to", "validTo"),
        serialization_alias="validTo",
    )
    supersedes_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("supersedes_id", "supersedesId"),
        serialization_alias="supersedesId",
    )
    embedding: list[float] | None = None

    model_config = ConfigDict(populate_by_name=True)

    @property
    def sourceIds(self) -> list[str]:
        return self.source_ids

    @property
    def validFrom(self) -> str | None:
        return self.valid_from

    @property
    def validTo(self) -> str | None:
        return self.valid_to

    @property
    def supersedesId(self) -> str | None:
        return self.supersedes_id


class ReviseInput(BaseModel):
    """Input payload to update an existing record with revision tracking."""

    text: str
    category: str | None = None
    expected_revision: int | None = Field(
        default=None,
        validation_alias=AliasChoices("expected_revision", "expectedRevision"),
        serialization_alias="expectedRevision",
    )
    source: str | None = None
    source_ids: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("source_ids", "sourceIds"),
        serialization_alias="sourceIds",
    )
    reason: str | None = None
    embedding: list[float] | None = None

    model_config = ConfigDict(populate_by_name=True)

    @property
    def expectedRevision(self) -> int | None:
        return self.expected_revision

    @property
    def sourceIds(self) -> list[str] | None:
        return self.source_ids


class SearchOptions(BaseModel):
    """Options for lexical or semantic memory queries."""

    kind: MemoryKind | None = None
    category: str | None = None
    limit: int = 10

    model_config = ConfigDict(populate_by_name=True)


class ListOptions(BaseModel):
    """Options for listing memory records."""

    kind: MemoryKind | None = None
    status: MemoryStatus = "active"
    limit: int = 50

    model_config = ConfigDict(populate_by_name=True)


class ContextAssemblyOptions(BaseModel):
    """Options for constructing bounded, labeled LLM memory context."""

    max_entries: int = Field(
        default=10,
        validation_alias=AliasChoices("max_entries", "maxEntries"),
        serialization_alias="maxEntries",
    )
    max_chars: int = Field(
        default=2000,
        validation_alias=AliasChoices("max_chars", "maxChars"),
        serialization_alias="maxChars",
    )
    max_profile_entries: int = Field(
        default=5,
        validation_alias=AliasChoices("max_profile_entries", "maxProfileEntries"),
        serialization_alias="maxProfileEntries",
    )
    max_search_entries: int = Field(
        default=5,
        validation_alias=AliasChoices("max_search_entries", "maxSearchEntries"),
        serialization_alias="maxSearchEntries",
    )

    model_config = ConfigDict(populate_by_name=True)

    @property
    def maxEntries(self) -> int:
        return self.max_entries

    @property
    def maxChars(self) -> int:
        return self.max_chars

    @property
    def maxProfileEntries(self) -> int:
        return self.max_profile_entries

    @property
    def maxSearchEntries(self) -> int:
        return self.max_search_entries


class MemoryRevisionRecord(BaseModel):
    """Historical revision audit entry for an updated memory record."""

    id: int | None = None
    record_id: str
    revision: int
    text: str
    category: str | None = None
    updated_at: str
    reason: str | None = None
    source: str | None = None
    source_ids: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class MemoryDeletionRecord(BaseModel):
    """Tombstone audit entry for a deleted memory record."""

    id: int | None = None
    record_id: str
    scope: str
    deleted_at: str
    reason: str | None = None

    model_config = ConfigDict(populate_by_name=True)


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for vector embedding providers."""

    dimension: int
    model: str

    def embed(self, text: str) -> Awaitable[list[float]] | list[float]:
        """Produce an embedding vector for the supplied text."""
        ...


__all__ = [
    "ContextAssemblyOptions",
    "EmbeddingProvider",
    "ListOptions",
    "MemoryDeletionRecord",
    "MemoryKind",
    "MemoryRecord",
    "MemoryRevisionRecord",
    "MemoryStatus",
    "MemoryTrust",
    "RememberInput",
    "ReviseInput",
    "SearchOptions",
]
