"""Pydantic schemas and data contracts for Agent-to-Agent (A2A 1.0) local teammate protocol.

Defines schemas for:
- AgentCard: Teammate discovery card (id, role, capabilities, budget, metadata).
- TaskRequest / TaskResult: Standardized request and result envelopes.
- ContextArtifact: Segregated data channel artifacts (untrusted data).
- TeammateEvent: Teammate status events for AG-UI UX-12 (spawned, step, finished).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from yusufclaw.orchestration.models import BudgetLimits

TaskStatus = Literal[
    "queued",
    "running",
    "success",
    "failed",
    "cancelled",
    "timeout",
    "budget_exceeded",
]


class TeammateBudget(BaseModel):
    """Child teammate budget limits."""

    model_config = ConfigDict(populate_by_name=True)

    max_tokens: int = Field(
        default=4000,
        validation_alias=AliasChoices("max_tokens", "maxTokens", "max_tokens_per_turn"),
        serialization_alias="maxTokens",
        description="Maximum tokens allowed for child execution",
    )
    max_steps: int = Field(
        default=8,
        validation_alias=AliasChoices("max_steps", "maxSteps", "max_steps_per_turn"),
        serialization_alias="maxSteps",
        description="Maximum model/tool steps allowed",
    )
    timeout_seconds: float = Field(
        default=60.0,
        validation_alias=AliasChoices(
            "timeout_seconds",
            "timeoutSeconds",
            "max_time_seconds_per_turn",
            "timeout",
        ),
        serialization_alias="timeoutSeconds",
        description="Maximum execution wall-clock time in seconds",
    )
    max_cost_usd: float = Field(
        default=0.50,
        validation_alias=AliasChoices("max_cost_usd", "maxCostUsd", "max_cost_per_turn"),
        serialization_alias="maxCostUsd",
        description="Maximum dollar cost allowed in USD",
    )

    def to_budget_limits(self) -> BudgetLimits:
        """Convert to orchestration BudgetLimits."""
        return BudgetLimits(
            max_tokens_per_turn=self.max_tokens,
            max_tokens_per_session=self.max_tokens * 4,
            max_cost_per_turn=self.max_cost_usd,
            max_cost_per_session=self.max_cost_usd * 4,
            max_steps_per_turn=self.max_steps,
            max_steps_per_session=self.max_steps * 4,
            max_time_seconds_per_turn=self.timeout_seconds,
            max_time_seconds_per_session=self.timeout_seconds * 4,
        )

    @classmethod
    def from_budget_limits(cls, limits: BudgetLimits) -> TeammateBudget:
        """Create from orchestration BudgetLimits."""
        return cls(
            max_tokens=limits.max_tokens_per_turn,
            max_steps=limits.max_steps_per_turn,
            timeout_seconds=limits.max_time_seconds_per_turn,
            max_cost_usd=limits.max_cost_per_turn,
        )


class ContextArtifact(BaseModel):
    """Context artifact passed via the segregated data channel.

    Child subagents can only read explicitly passed context artifacts,
    never the parent agent's full memory store. All context artifacts
    are labeled untrusted data to prevent prompt injection traversal.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(
        default_factory=lambda: f"art_{uuid.uuid4().hex[:10]}",
        description="Unique identifier of the context artifact",
    )
    name: str = Field(..., description="Human-readable name or filename of the artifact")
    content: str = Field(..., description="Raw text or JSON content of the artifact")
    mime_type: str = Field(
        default="text/plain",
        validation_alias=AliasChoices("mime_type", "mimeType"),
        serialization_alias="mimeType",
        description="MIME type of the artifact",
    )
    untrusted: bool = Field(
        default=True,
        description="Integrity label marking the artifact as untrusted reference data",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional provenance or source metadata",
    )


class AgentCard(BaseModel):
    """A2A 1.0 compatible teammate card declaring identity, role, capabilities, and budget."""

    model_config = ConfigDict(populate_by_name=True)

    protocol_version: str = Field(
        default="a2a/1.0",
        validation_alias=AliasChoices("protocol_version", "protocolVersion"),
        serialization_alias="protocolVersion",
        description="Agent-to-Agent protocol version advertised by this card",
    )
    id: str = Field(
        ...,
        validation_alias=AliasChoices("id", "agent_id", "agentId"),
        serialization_alias="id",
        description="Unique teammate instance identifier (e.g. 'teammate_researcher_1')",
    )
    role: str = Field(
        default="general",
        description="Role identifier (e.g. 'researcher', 'coder', 'writer', 'ops')",
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description="Declared functional capabilities and tool allowlist for this teammate",
    )
    budget: TeammateBudget = Field(
        default_factory=TeammateBudget,
        description="Enforced token, step, time, and cost budget ceilings",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_budget(cls, data: Any) -> Any:
        if isinstance(data, dict):
            b = data.get("budget")
            if isinstance(b, BudgetLimits):
                data["budget"] = TeammateBudget.from_budget_limits(b)
        return data
    name: str | None = Field(
        default=None,
        description="Optional human-readable display name",
    )
    description: str = Field(
        default="",
        description="Teammate purpose, specialization, and operational domain",
    )
    version: str = Field(
        default="1.0.0",
        description="SemVer protocol / agent card specification version",
    )
    endpoint: str | None = Field(
        default=None,
        description="Optional HTTP JSON transport endpoint URL (e.g. 'http://127.0.0.1:8000')",
    )
    skills: list[str] = Field(
        default_factory=list,
        description="Declared skills available to this teammate",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary extension metadata",
    )

    @property
    def display_name(self) -> str:
        """Return display name or fallback to id."""
        return self.name or self.id


class TaskRequest(BaseModel):
    """A2A task request envelope dispatched to a teammate."""

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(
        default_factory=lambda: f"task_{uuid.uuid4().hex[:12]}",
        validation_alias=AliasChoices("task_id", "taskId"),
        serialization_alias="taskId",
        description="Unique task dispatch identifier",
    )
    parent_task_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("parent_task_id", "parentTaskId"),
        serialization_alias="parentTaskId",
        description="Identifier of the parent task that spawned this request",
    )
    caller_id: str = Field(
        default="parent",
        validation_alias=AliasChoices("caller_id", "callerId"),
        serialization_alias="callerId",
        description="Principal or agent identifier of the caller",
    )
    target_agent_id: str = Field(
        ...,
        validation_alias=AliasChoices("target_agent_id", "targetAgentId"),
        serialization_alias="targetAgentId",
        description="Identifier of the target teammate to execute the task",
    )
    instruction: str = Field(
        ...,
        description="Trusted instruction command for the teammate",
    )
    context_artifacts: list[ContextArtifact] = Field(
        default_factory=list,
        validation_alias=AliasChoices("context_artifacts", "contextArtifacts"),
        serialization_alias="contextArtifacts",
        description="Segregated data channel: explicitly passed context artifacts only",
    )
    budget: TeammateBudget = Field(
        default_factory=TeammateBudget,
        description="Child budget cap for this specific task invocation",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_budget(cls, data: Any) -> Any:
        if isinstance(data, dict):
            b = data.get("budget")
            if isinstance(b, BudgetLimits):
                data["budget"] = TeammateBudget.from_budget_limits(b)
        return data
    session_id: str = Field(
        default="default",
        validation_alias=AliasChoices("session_id", "sessionId"),
        serialization_alias="sessionId",
        description="Caller session scope identifier",
    )
    depth: int = Field(
        default=1,
        description="Subagent recursion depth (strictly locked to depth <= 1)",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        validation_alias=AliasChoices("created_at", "createdAt"),
        serialization_alias="createdAt",
        description="UTC creation timestamp",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Transport or execution metadata",
    )


class TaskResult(BaseModel):
    """A2A task result envelope returned by a teammate."""

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(
        ...,
        validation_alias=AliasChoices("task_id", "taskId"),
        serialization_alias="taskId",
        description="Identifier matching the original TaskRequest",
    )
    agent_id: str = Field(
        ...,
        validation_alias=AliasChoices("agent_id", "agentId"),
        serialization_alias="agentId",
        description="Identifier of the teammate that executed the task",
    )
    status: TaskStatus = Field(
        ...,
        description="Execution status outcome",
    )
    output: str = Field(
        default="",
        description="Primary textual output or answer returned by the teammate",
    )
    artifacts: list[ContextArtifact] = Field(
        default_factory=list,
        description="Artifacts produced during task execution",
    )
    tokens_used: int = Field(
        default=0,
        validation_alias=AliasChoices("tokens_used", "tokensUsed"),
        serialization_alias="tokensUsed",
        description="Total tokens consumed during child execution",
    )
    steps_used: int = Field(
        default=0,
        validation_alias=AliasChoices("steps_used", "stepsUsed"),
        serialization_alias="stepsUsed",
        description="Number of model/tool steps taken",
    )
    duration_ms: float = Field(
        default=0.0,
        validation_alias=AliasChoices("duration_ms", "durationMs"),
        serialization_alias="durationMs",
        description="Execution elapsed duration in milliseconds",
    )
    error: str | None = Field(
        default=None,
        description="Error details if status is failed, timeout, or cancelled",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Execution or telemetry metadata",
    )
