"""Pydantic data models for AG-UI 1.0 protocol and interactive UX widgets.

Standardizes typed server-sent events (SSE) envelopes, milestone progress,
inline decision questions, verifiable action cards, and masked secret requests
according to docs/UX-SPEC.md §3 and §4.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AG_UI_PROTOCOL_VERSION = "ag-ui/1.0"


class AgUiEnvelope(BaseModel):
    """Canonical AG-UI 1.0 event envelope schema (docs/UX-SPEC.md §3.1)."""

    model_config = ConfigDict(populate_by_name=True)

    protocol_version: str = Field(
        default=AG_UI_PROTOCOL_VERSION, alias="protocolVersion"
    )
    event_id: str = Field(alias="eventId")
    run_id: str = Field(alias="runId")
    session_id: str = Field(alias="sessionId")
    agent_id: str = Field(default="orchestrator", alias="agentId")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    sequence: int = 1
    event_type: str = Field(alias="eventType")
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        event_type: str,
        payload: dict[str, Any],
        run_id: str,
        session_id: str,
        agent_id: str = "orchestrator",
        sequence: int = 1,
        event_id: str | None = None,
    ) -> AgUiEnvelope:
        """Create a strongly typed AG-UI envelope."""
        return cls(
            protocolVersion=AG_UI_PROTOCOL_VERSION,
            eventId=event_id or f"evt_{uuid.uuid4().hex[:18]}",
            runId=run_id,
            sessionId=session_id,
            agentId=agent_id,
            timestamp=datetime.now(UTC).isoformat(),
            sequence=sequence,
            eventType=event_type,
            payload=payload,
        )


class MilestoneStep(BaseModel):
    """Progress milestone within an asynchronous background execution loop (UX-03)."""

    model_config = ConfigDict(populate_by_name=True)

    step: int
    total_steps: int = Field(default=1, alias="totalSteps")
    title: str
    status: Literal["running", "completed", "failed"] = "running"
    detail: str = ""
    duration_ms: int = Field(default=0, alias="durationMs")


class QuestionOption(BaseModel):
    """Selectable option chip inside an inline decision widget (UX-04)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    label: str
    detail: str = ""
    description: str | None = None
    recommended: bool = False


class QuestionWidget(BaseModel):
    """Inline decision widget prompting user for discrete choices (UX-04)."""

    model_config = ConfigDict(populate_by_name=True)

    question_id: str = Field(alias="questionId")
    prompt: str
    options: list[QuestionOption]
    is_multi_select: bool = Field(default=False, alias="isMultiSelect")
    timeout_ms: int = Field(default=300_000, alias="timeoutMs")


class WidgetResolveInput(BaseModel):
    """Input payload submitting user decision for an inline question widget."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    question_id: str = Field(min_length=1, max_length=128, alias="questionId")
    selected_option_ids: list[str] = Field(default_factory=list, alias="selectedOptionIds")
    custom_text: str | None = Field(default=None, max_length=4096, alias="customText")

    @property
    def questionId(self) -> str:
        return self.question_id

    @property
    def selectedOptionIds(self) -> list[str]:
        return self.selected_option_ids

    @property
    def customText(self) -> str | None:
        return self.custom_text


class SecretRequestWidget(BaseModel):
    """Masked secret-request form prompt sent to the client (UX-06)."""

    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field(alias="requestId")
    key_name: str = Field(alias="keyName")
    service_name: str = Field(default="", alias="serviceName")
    target_host: str = Field(default="", alias="targetHost")
    description: str = ""
    destination: str = ""
    ephemeral_public_key: str = Field(default="", alias="ephemeralPublicKey")
    storage_policy: str = Field(
        default="Stored in encrypted local keyring, never written to SQLite conversation turns or transmitted to model prompts.",
        alias="storagePolicy",
    )


class SecretSubmitInput(BaseModel):
    """Incoming masked secret submission payload from client modal dialog (UX-06)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: str = Field(min_length=1, max_length=128, alias="requestId")
    key_name: str = Field(min_length=1, max_length=128, alias="keyName")
    secret_value: str | None = Field(default=None, max_length=8192, alias="secretValue")
    value: str | None = Field(default=None, max_length=8192)
    ciphertext: str | None = Field(default=None, max_length=8192)
    service_name: str | None = Field(default=None, max_length=128, alias="serviceName")

    def resolve_secret(self) -> str:
        """Resolve the secret string from supported aliases."""
        val = self.secret_value or self.value or self.ciphertext
        if not val or not val.strip():
            raise ValueError("Secret value cannot be empty")
        return val.strip()

    @property
    def requestId(self) -> str:
        return self.request_id

    @property
    def keyName(self) -> str:
        return self.key_name


class AgentPersona(BaseModel):
    """Named specialist agent descriptor displayed in the sidebar (UX-02)."""

    model_config = ConfigDict(populate_by_name=True)

    agent_id: str = Field(alias="agentId")
    name: str
    role: str
    model: str
    status: Literal["online", "idle", "busy"] = "online"
    description: str = ""
    allowed_tools: list[str] = Field(default_factory=list, alias="allowedTools")


class MemoryScopeQuery(BaseModel):
    """Query parameters locating a web session memory scope (UX-07)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    agent_id: str = Field(default="orchestrator", min_length=1, max_length=64, alias="agentId")
    conversation_id: str = Field(
        default="default", min_length=1, max_length=128, alias="conversationId"
    )
    limit: int = Field(default=50, ge=1, le=200)

    @property
    def agentId(self) -> str:
        return self.agent_id

    @property
    def conversationId(self) -> str:
        return self.conversation_id


class MemoryForgetInput(BaseModel):
    """Confirm deterministic unlearning of a durable memory record (UX-07)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    record_id: str = Field(min_length=1, max_length=128, alias="recordId")
    agent_id: str = Field(default="orchestrator", min_length=1, max_length=64, alias="agentId")
    conversation_id: str = Field(
        default="default", min_length=1, max_length=128, alias="conversationId"
    )
    reason: str | None = Field(default=None, max_length=512)

    @property
    def recordId(self) -> str:
        return self.record_id

    @property
    def agentId(self) -> str:
        return self.agent_id

    @property
    def conversationId(self) -> str:
        return self.conversation_id


class MemoryReviseApiInput(BaseModel):
    """In-place edit of an active memory claim with CAS revision guard (UX-07)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    text: str = Field(min_length=1, max_length=8000)
    agent_id: str = Field(default="orchestrator", min_length=1, max_length=64, alias="agentId")
    conversation_id: str = Field(
        default="default", min_length=1, max_length=128, alias="conversationId"
    )
    expected_revision: int | None = Field(default=None, ge=1, alias="expectedRevision")
    category: str | None = Field(default=None, max_length=128)
    reason: str | None = Field(default=None, max_length=512)

    @property
    def agentId(self) -> str:
        return self.agent_id

    @property
    def conversationId(self) -> str:
        return self.conversation_id

    @property
    def expectedRevision(self) -> int | None:
        return self.expected_revision


__all__ = [
    "AG_UI_PROTOCOL_VERSION",
    "AgUiEnvelope",
    "AgentPersona",
    "MemoryForgetInput",
    "MemoryReviseApiInput",
    "MemoryScopeQuery",
    "MilestoneStep",
    "QuestionOption",
    "QuestionWidget",
    "SecretRequestWidget",
    "SecretSubmitInput",
    "WidgetResolveInput",
]
