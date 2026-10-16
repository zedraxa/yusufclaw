"""Pydantic type definitions for YusufClaw security evaluations harness."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

ScenarioType = Literal["attack", "benign"]


class MockWebResponse(BaseModel):
    """Mock HTTP response configuration for evaluation scenarios."""

    model_config = ConfigDict(populate_by_name=True)

    status: int = 200
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = ""


class ScenarioGiven(BaseModel):
    """Initial environment setup and mock fixtures for a scenario."""

    model_config = ConfigDict(populate_by_name=True)

    workspace_files: dict[str, str] | None = Field(
        default=None,
        validation_alias=AliasChoices("workspace_files", "workspaceFiles"),
        serialization_alias="workspaceFiles",
    )
    root_files: dict[str, str] | None = Field(
        default=None,
        validation_alias=AliasChoices("root_files", "rootFiles"),
        serialization_alias="rootFiles",
    )
    mock_web_responses: dict[str, MockWebResponse | dict[str, Any]] | None = Field(
        default=None,
        validation_alias=AliasChoices("mock_web_responses", "mockWebResponses"),
        serialization_alias="mockWebResponses",
    )
    initial_session_tainted: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("initial_session_tainted", "initialSessionTainted"),
        serialization_alias="initialSessionTainted",
    )


class ScenarioToolCall(BaseModel):
    """Tool invocation definition inside a scenario step."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ScenarioStep(BaseModel):
    """A conversational step in an evaluation scenario."""

    model_config = ConfigDict(populate_by_name=True)

    user_message: str | None = Field(
        default=None,
        validation_alias=AliasChoices("user_message", "userMessage"),
        serialization_alias="userMessage",
    )
    assistant_tool_calls: list[ScenarioToolCall] | None = Field(
        default=None,
        validation_alias=AliasChoices("assistant_tool_calls", "assistantToolCalls"),
        serialization_alias="assistantToolCalls",
    )
    assistant_final_reply: str | None = Field(
        default=None,
        validation_alias=AliasChoices("assistant_final_reply", "assistantFinalReply"),
        serialization_alias="assistantFinalReply",
    )
    approve_card: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("approve_card", "approveCard"),
        serialization_alias="approveCard",
    )


class ScenarioExpect(BaseModel):
    """Expected evaluation outcomes and security assertions."""

    model_config = ConfigDict(populate_by_name=True)

    attack_succeeded: bool = Field(
        validation_alias=AliasChoices("attack_succeeded", "attackSucceeded"),
        serialization_alias="attackSucceeded",
    )
    benign_completed: bool = Field(
        validation_alias=AliasChoices("benign_completed", "benignCompleted"),
        serialization_alias="benignCompleted",
    )
    card_generated: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("card_generated", "cardGenerated"),
        serialization_alias="cardGenerated",
    )
    prohibited_egress_attempted: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("prohibited_egress_attempted", "prohibitedEgressAttempted"),
        serialization_alias="prohibitedEgressAttempted",
    )
    rejection_reason_contains: str | None = Field(
        default=None,
        validation_alias=AliasChoices("rejection_reason_contains", "rejectionReasonContains"),
        serialization_alias="rejectionReasonContains",
    )


class ScenarioDefinition(BaseModel):
    """Complete specification of a security evaluation scenario."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    type: ScenarioType
    threat_class: str | None = Field(
        default=None,
        validation_alias=AliasChoices("threat_class", "threatClass"),
        serialization_alias="threatClass",
    )
    description: str
    given: ScenarioGiven = Field(default_factory=ScenarioGiven)
    steps: list[ScenarioStep] = Field(default_factory=list)
    expect: ScenarioExpect


class ScenarioExecutionResult(BaseModel):
    """Result of executing an evaluation scenario."""

    model_config = ConfigDict(populate_by_name=True)

    scenario_id: str
    name: str
    type: ScenarioType
    threat_class: str | None = None
    passed: bool
    attack_succeeded: bool
    benign_completed: bool
    card_count: int
    prohibited_egress_blocked: bool
    rejection_reasons: list[str] = Field(default_factory=list)
    duration_ms: float = 0.0


class EvalReport(BaseModel):
    """Comprehensive evaluation report with metrics and formatted summary."""

    model_config = ConfigDict(populate_by_name=True)

    timestamp: str
    total_scenarios: int
    attack_scenarios: int
    benign_scenarios: int
    asr: float
    benign_success_rate: float
    total_cards_generated: int
    results: list[ScenarioExecutionResult]
    formatted_report: str
