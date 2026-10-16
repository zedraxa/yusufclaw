"""Teammate event schemas for AG-UI UX-12 telemetry.

Defined separately from models.py to prevent circular import dependencies
with orchestration subagent runner.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

TeammateEventType = Literal[
    "teammate.spawned",
    "teammate.step",
    "teammate.finished",
]


class TeammateEvent(BaseModel):
    """Teammate status event matching AG-UI UX-12 specification."""

    model_config = ConfigDict(populate_by_name=True)

    event_type: TeammateEventType = Field(
        ...,
        validation_alias=AliasChoices("event_type", "eventType"),
        serialization_alias="eventType",
        description="Event type name ('teammate.spawned', 'teammate.step', 'teammate.finished')",
    )
    subagent_id: str = Field(
        ...,
        validation_alias=AliasChoices("subagent_id", "subagentId"),
        serialization_alias="subagentId",
        description="Identifier of the child subagent/teammate",
    )
    parent_run_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("parent_run_id", "parentRunId"),
        serialization_alias="parentRunId",
        description="Parent run or task identifier",
    )
    role: str | None = Field(
        default=None,
        description="Specialist role of the teammate",
    )
    task: str | None = Field(
        default=None,
        description="Instruction or task description being executed",
    )
    tools: list[str] = Field(
        default_factory=list,
        description="Permitted tools granted to this teammate",
    )
    max_cost: float | None = Field(
        default=None,
        validation_alias=AliasChoices("max_cost", "maxCost"),
        serialization_alias="maxCost",
        description="Budget ceiling in USD",
    )
    step: int | None = Field(
        default=None,
        description="Monotonic execution step index",
    )
    summary: str | None = Field(
        default=None,
        description="Brief progress summary for the current step",
    )
    tokens: int | None = Field(
        default=None,
        description="Cumulative tokens used up to this step",
    )
    status: str | None = Field(
        default=None,
        description="Final status on finish ('success', 'failed', 'cancelled')",
    )
    artifact_ref: str | None = Field(
        default=None,
        validation_alias=AliasChoices("artifact_ref", "artifactRef"),
        serialization_alias="artifactRef",
        description="Identifier or path of produced artifact",
    )
    cost_usd: float | None = Field(
        default=None,
        validation_alias=AliasChoices("cost_usd", "costUsd"),
        serialization_alias="costUsd",
        description="Estimated cost in USD upon completion",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Event occurrence timestamp",
    )
