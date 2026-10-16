"""YusufClaw Adaptive Orchestration Subpackage.

Implements the P1 orchestration and verification architecture:
1. Task Analyzer: Deterministic heuristic and model-assisted task classification
   (complexity, risk, tools, strategy).
2. Budget Manager: Token, cost, step, and time tracking per turn and per session
   with configurable ceilings and graceful stop.
3. Verifier: Acceptance criteria validation (rules and model judge) with automated
   1-turn retry.
4. Plan-and-Execute / Subagents: LangGraph state machine with Deep Agents support
   for decomposed planning and bounded specialist subagents (depth 1, least privilege).
5. Adaptive Orchestrator: Unified coordinator with OpenTelemetry distributed tracing.
"""

from yusufclaw.orchestration.budget import BudgetManager
from yusufclaw.orchestration.models import (
    AcceptanceCriteria,
    BudgetExceededError,
    BudgetLimits,
    BudgetStatus,
    BudgetUsage,
    ComplexityLevel,
    ExecutionPlan,
    OrchestrationConfig,
    OrchestrationStrategy,
    PlanStep,
    RiskLevel,
    SubagentResult,
    SubagentSpec,
    TaskAnalysis,
    TokenPricing,
    VerificationResult,
)
from yusufclaw.orchestration.orchestrator import AdaptiveOrchestrator
from yusufclaw.orchestration.planner import PlanAndExecuteWorkflow
from yusufclaw.orchestration.subagent import DEEP_AGENTS_AVAILABLE, SubagentRunner
from yusufclaw.orchestration.task_analyzer import TaskAnalyzer
from yusufclaw.orchestration.verifier import Verifier
from yusufclaw.writers import WriterDelegateRunner, WriterResult, WriterSpec

__all__ = [
    "DEEP_AGENTS_AVAILABLE",
    "AcceptanceCriteria",
    "AdaptiveOrchestrator",
    "BudgetExceededError",
    "BudgetLimits",
    "BudgetManager",
    "BudgetStatus",
    "BudgetUsage",
    "ComplexityLevel",
    "ExecutionPlan",
    "OrchestrationConfig",
    "OrchestrationStrategy",
    "PlanAndExecuteWorkflow",
    "PlanStep",
    "RiskLevel",
    "SubagentResult",
    "SubagentRunner",
    "SubagentSpec",
    "TaskAnalysis",
    "TaskAnalyzer",
    "TokenPricing",
    "VerificationResult",
    "Verifier",
    "WriterDelegateRunner",
    "WriterResult",
    "WriterSpec",
]
