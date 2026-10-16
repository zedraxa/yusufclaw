"""Pydantic models for YusufClaw security subsystem.

Implements typed security primitives:
- Label & Value: Data classification, provenance tracking, and sensitivity labels.
- SessionTaint: Contamination state and untrusted source tracking across turns.
- ToolManifest: Tool capabilities, effects, sources, and resources.
- ActionCard & CardRecord: Nonce-protected, tamper-evident human-in-the-loop approvals.
- PolicyDecision: Determinist pre-execution authorization outcomes.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

Integrity = Literal["trusted", "untrusted"]
Secrecy = Literal["public", "private", "secret"]

ToolEffect = Literal[
    "read_private",
    "read_untrusted",
    "egress",
    "write_local",
    "exec",
    "send_message",
    "spend",
]

ToolSource = Literal["builtin", "mcp", "skill"]


class Label(BaseModel):
    """Security classification label attached to data."""

    model_config = ConfigDict(populate_by_name=True)

    origin: str
    integrity: Integrity = "trusted"
    secrecy: Secrecy = "public"


class Value[T](BaseModel):
    """Wrapper holding a value alongside its provenance/security labels."""

    model_config = ConfigDict(populate_by_name=True)

    v: T
    labels: list[Label] = Field(default_factory=list)


class SessionTaint(BaseModel):
    """Tracks whether a session has ingested untrusted content and its sources."""

    model_config = ConfigDict(populate_by_name=True)

    contaminated: bool = False
    sources: list[str] = Field(default_factory=list)


class ToolResources(BaseModel):
    """Resource constraints declared by a tool."""

    model_config = ConfigDict(populate_by_name=True)

    paths: list[str] = Field(default_factory=list)
    hosts: list[str] = Field(default_factory=list)


class ToolManifest(BaseModel):
    """Security manifest for an agent tool defining effects, source, and bounds."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    version: str = "1.0.0"
    source: ToolSource = "builtin"
    effects: list[ToolEffect] = Field(default_factory=list)
    resources: ToolResources | None = None
    description_sha256: str | None = Field(
        default=None,
        validation_alias=AliasChoices("description_sha256", "descriptionSha256"),
        serialization_alias="descriptionSha256",
    )


class ActionCardRendered(BaseModel):
    """Deterministic, human-verifiable representation of an action to approve."""

    model_config = ConfigDict(populate_by_name=True)

    title: str
    exact: str
    effects: list[ToolEffect]
    taint: Literal["clean", "contaminated"]
    reversible: bool
    uninspectable: str | None = None


class ActionCard(BaseModel):
    """Human-in-the-loop approval card with cryptographic nonce and argument hash."""

    model_config = ConfigDict(populate_by_name=True)

    approval_id: str = Field(
        validation_alias=AliasChoices("approval_id", "approvalId"),
        serialization_alias="approvalId",
    )
    nonce: str
    expires_at: str = Field(
        validation_alias=AliasChoices("expires_at", "expiresAt"),
        serialization_alias="expiresAt",
    )
    tool_id: str = Field(
        validation_alias=AliasChoices("tool_id", "toolId"),
        serialization_alias="toolId",
    )
    tool_version: str = Field(
        validation_alias=AliasChoices("tool_version", "toolVersion"),
        serialization_alias="toolVersion",
    )
    args_sha256: str = Field(
        validation_alias=AliasChoices("args_sha256", "argsSha256"),
        serialization_alias="argsSha256",
    )
    rendered: ActionCardRendered


class PolicyDecision(BaseModel):
    """Outcome of evaluating a tool invocation against policy rules."""

    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["allow", "deny", "card"]
    reason: str | None = None
    card: ActionCard | None = None


CardStatus = Literal["pending", "consumed", "expired", "denied"]


class CardRecord(BaseModel):
    """Internal storage record for an ActionCard with lifecycle state."""

    model_config = ConfigDict(populate_by_name=True)

    card: ActionCard
    status: CardStatus = "pending"
    created_at: float = Field(
        default_factory=time.time,
        validation_alias=AliasChoices("created_at", "createdAt"),
        serialization_alias="createdAt",
    )
    expected_args: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("expected_args", "expectedArgs"),
        serialization_alias="expectedArgs",
    )


class ConsumeResult(BaseModel):
    """Result of attempting to consume or verify an ActionCard."""

    model_config = ConfigDict(populate_by_name=True)

    success: bool
    reason: str | None = None
    card: ActionCard | None = None
