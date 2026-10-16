"""Data models and type definitions for YusufClaw adaptive orchestration.

Defines schemas for:
- Task analysis (complexity, risk, tools, strategy)
- Budget limits and usage tracking
- Verifier acceptance criteria and results
- Subagent specifications and execution results
- Execution plans and step-by-step progress
- Orchestration configuration
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

ComplexityLevel = Literal["simple", "medium", "complex"]
RiskLevel = Literal["low", "medium", "high", "critical"]
OrchestrationStrategy = Literal["direct", "plan_and_execute", "delegate"]


class TaskAnalysis(BaseModel):
    """Result of classifying an incoming user request."""

    complexity: ComplexityLevel = "simple"
    required_tools: list[str] = Field(default_factory=list)
    risk: RiskLevel = "low"
    plan_required: bool = False
    delegate_required: bool = False
    strategy: OrchestrationStrategy = "direct"
    reasoning: str = ""
    subtasks: list[str] = Field(default_factory=list)
    estimated_steps: int = 1


class TokenPricing(BaseModel):
    """Cost per million tokens in USD."""

    prompt_cost_per_1m: float = 3.0  # $3.00 per 1M prompt tokens
    completion_cost_per_1m: float = 15.0  # $15.00 per 1M completion tokens


class BudgetLimits(BaseModel):
    """Configurable budget ceilings per turn and per session."""

    max_tokens_per_turn: int = 8192
    max_tokens_per_session: int = 32768
    max_cost_per_turn: float = 0.50  # USD
    max_cost_per_session: float = 5.00  # USD
    max_steps_per_turn: int = 8
    max_steps_per_session: int = 40
    max_time_seconds_per_turn: float = 60.0
    max_time_seconds_per_session: float = 600.0


class BudgetUsage(BaseModel):
    """Observed resource consumption."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    steps: int = 0
    elapsed_seconds: float = 0.0


class BudgetStatus(BaseModel):
    """Status assessment against configured budget limits."""

    is_exceeded: bool = False
    exceeded_reason: str | None = None
    remaining_tokens_turn: int = 0
    remaining_cost_turn: float = 0.0
    remaining_steps_turn: int = 0
    remaining_time_turn: float = 0.0
    remaining_tokens_session: int = 0
    remaining_cost_session: float = 0.0
    remaining_steps_session: int = 0
    remaining_time_session: float = 0.0


class BudgetExceededError(RuntimeError):
    """Raised when an operation exceeds allocated budget limits."""

    def __init__(
        self,
        limit_name: str,
        current_value: float | int,
        limit_value: float | int,
        scope: str = "turn",
    ) -> None:
        super().__init__(
            f"Budget limit exceeded [{scope}]: {limit_name} is {current_value}, limit is {limit_value}"
        )
        self.limit_name = limit_name
        self.current_value = current_value
        self.limit_value = limit_value
        self.scope = scope


class AcceptanceCriteria(BaseModel):
    """Criteria against which an agent/turn output is verified."""

    min_length: int = 1
    max_length: int | None = None
    required_keywords: list[str] = Field(default_factory=list)
    forbidden_keywords: list[str] = Field(default_factory=list)
    required_pattern: str | None = None
    must_not_contain_patterns: list[str] = Field(default_factory=list)
    require_tool_execution: bool = False
    expected_tools: list[str] = Field(default_factory=list)
    model_judge_required: bool = False
    custom_judge_rubric: str | None = None


class VerificationResult(BaseModel):
    """Outcome of validating a candidate output."""

    passed: bool
    score: float = 1.0  # Normalized 0.0 to 1.0
    reasons: list[str] = Field(default_factory=list)
    retry_recommended: bool = False
    retry_prompt: str | None = None
    rule_results: dict[str, bool] = Field(default_factory=dict)
    retries_attempted: int = 0


class SubagentSpec(BaseModel):
    """Specification for launching a child worker/subagent (following RESEARCH-ASIST.md)."""

    name: str
    instruction: str  # Trusted instruction from parent or user plan
    context_data: list[str] = Field(default_factory=list)  # Untrusted data channel
    permitted_tools: list[str] = Field(default_factory=list)  # Subset of parent grants
    model_role: str = "default"
    max_steps: int = 4
    max_tokens: int = 4096
    timeout_seconds: float = 30.0
    max_depth: int = 1


class SubagentResult(BaseModel):
    """Structured response returned by a subagent."""

    subagent_name: str
    success: bool
    output: str
    tokens_used: int = 0
    steps_used: int = 0
    error: str | None = None


StepStatus = Literal["pending", "in_progress", "completed", "failed", "skipped"]


class PlanStep(BaseModel):
    """A discrete step in an execution plan."""

    step_id: int
    description: str
    tool_or_subagent: str | None = None
    status: StepStatus = "pending"
    result: str | None = None
    error: str | None = None


class ExecutionPlan(BaseModel):
    """Decomposed multi-step plan for a complex task."""

    goal: str
    steps: list[PlanStep] = Field(default_factory=list)
    current_step_index: int = 0
    is_completed: bool = False
    final_synthesis: str | None = None


class OrchestrationConfig(BaseModel):
    """Master configuration for adaptive orchestration."""

    enabled: bool = False
    model_assisted_analysis: bool = False
    model_judge_verification: bool = False
    auto_retry_verification: bool = True
    max_retries: int = 1
    budget_limits: BudgetLimits = Field(default_factory=BudgetLimits)
    token_pricing: TokenPricing = Field(default_factory=TokenPricing)
    use_deep_agents_if_available: bool = True


RuleCheckFn = Callable[[str], tuple[bool, str]]
