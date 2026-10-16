"""Adaptive meta-orchestrator coordinating analysis, budget, planning, and verification.

Implements the YusufClaw adaptive orchestration lifecycle:
1. Task analysis (complexity, risk, tools, strategy).
2. Budget lifecycle (turn initialization, token/cost limits, graceful degradation).
3. Strategy routing:
   - Direct path: Single agent execution loop.
   - Plan-and-execute path: LangGraph structured decomposition.
   - Subagent path: Delegated specialist workers.
4. Output verification against criteria with automated 1-turn retry.
5. OpenTelemetry distributed tracing and metrics.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence

from yusufclaw.models.router import ModelRole, ModelRouter
from yusufclaw.models.types import Message, ModelProvider, ModelRequest, ToolDefinition
from yusufclaw.orchestration.budget import BudgetManager
from yusufclaw.orchestration.models import (
    AcceptanceCriteria,
    OrchestrationConfig,
    TaskAnalysis,
)
from yusufclaw.orchestration.planner import PlanAndExecuteWorkflow
from yusufclaw.orchestration.subagent import SubagentRunner
from yusufclaw.orchestration.task_analyzer import TaskAnalyzer
from yusufclaw.orchestration.verifier import Verifier
from yusufclaw.security import Grants
from yusufclaw.telemetry import get_tracer

logger = logging.getLogger(__name__)

DirectTurnFn = Callable[[], Awaitable[str]] | Callable[[ModelProvider | None], Awaitable[str]]
DirectRetryFn = Callable[[str], Awaitable[str]]


class AdaptiveOrchestrator:
    """Coordinates task analysis, budget limits, plan execution, and verification."""

    def __init__(
        self,
        provider: ModelProvider | None = None,
        config: OrchestrationConfig | None = None,
        tools: Sequence[ToolDefinition] | None = None,
        grants: Grants | None = None,
        router: ModelRouter | None = None,
        providers: Mapping[str, ModelProvider] | None = None,
    ) -> None:
        self.config = config or OrchestrationConfig(enabled=True)
        self.tools = list(tools) if tools else []
        self.grants = grants

        # Setup model router
        if router is not None:
            self.router = router
            self.provider = provider or router.get_provider(ModelRole.DEFAULT)
        elif providers is not None:
            self.router = ModelRouter(
                providers=providers,
                default_provider=provider,
                config=self.config.routing,
            )
            self.provider = provider or self.router.get_provider(ModelRole.DEFAULT)
        elif provider is not None:
            self.provider = provider
            self.router = ModelRouter(
                default_provider=provider,
                config=self.config.routing,
            )
        else:
            from yusufclaw.models import get_model_provider

            self.provider = get_model_provider()
            self.router = ModelRouter(
                default_provider=self.provider,
                config=self.config.routing,
            )

        # Initialize subcomponents with routed providers
        self.task_analyzer = TaskAnalyzer(
            provider=self.router.get_provider(ModelRole.CLASSIFY),
            available_tools=self.tools,
            router=self.router,
        )
        self.budget_manager = BudgetManager(
            limits=self.config.budget_limits,
            pricing=self.config.token_pricing,
        )
        self.verifier = Verifier(
            provider=self.router.get_provider(ModelRole.JUDGE),
            router=self.router,
        )
        self.subagent_runner = SubagentRunner(
            provider=self.provider,
            parent_tools=self.tools,
            parent_grants=self.grants,
            budget_manager=self.budget_manager,
            use_deep_agents=self.config.use_deep_agents_if_available,
            router=self.router,
        )
        self.planner = PlanAndExecuteWorkflow(
            provider=self.router.get_provider(ModelRole.PLANNER),
            subagent_runner=self.subagent_runner,
            budget_manager=self.budget_manager,
            verifier=self.verifier,
            available_tools=self.tools,
            router=self.router,
        )

    async def orchestrate_turn(
        self,
        prompt: str,
        session_id: str,
        direct_turn_fn: DirectTurnFn,
        *,
        criteria: AcceptanceCriteria | None = None,
        direct_retry_fn: DirectRetryFn | None = None,
    ) -> tuple[str, TaskAnalysis]:
        """Execute a conversational turn with adaptive orchestration, budget, and verification."""
        tracer = get_tracer("yusufclaw.orchestration")
        with tracer.start_as_current_span("yusufclaw.orchestration.turn") as span:
            if span.is_recording():
                span.set_attribute("yusufclaw.orchestration.session_id", session_id)

            # 1. Budget check at turn start
            self.budget_manager.start_turn()
            initial_status = self.budget_manager.check_limits()
            if initial_status.is_exceeded:
                msg = self.budget_manager.get_graceful_stop_message(initial_status.exceeded_reason)
                analysis = self.task_analyzer.analyze_heuristic(prompt)
                return msg, analysis

            # 2. Task Analysis
            analysis = await self.task_analyzer.analyze(
                prompt,
                model_assisted=self.config.model_assisted_analysis,
            )

            # 3. Strategy Routing
            candidate_output = ""
            if analysis.strategy in ("plan_and_execute", "delegate"):
                initial_plan = self.planner._build_plan_from_analysis(prompt, analysis)
                candidate_output = await self.planner.execute(prompt, initial_plan=initial_plan)
            else:
                # Direct agent execution loop
                # Prefer cheap provider for simple and low-risk tasks when configured
                routed_direct_provider: ModelProvider | None = None
                if self.router is not None:
                    routed_direct_provider = self.router.get_provider(
                        role=ModelRole.DEFAULT,
                        analysis=analysis,
                    )

                sig = inspect.signature(direct_turn_fn)
                if len(sig.parameters) > 0:
                    candidate_output = await direct_turn_fn(routed_direct_provider)  # type: ignore[call-arg]
                else:
                    candidate_output = await direct_turn_fn()  # type: ignore[call-arg]

            # 4. Check budget after execution
            budget_status = self.budget_manager.check_limits()
            if budget_status.is_exceeded:
                self.budget_manager.end_turn()
                return (
                    self.budget_manager.get_graceful_stop_message(budget_status.exceeded_reason),
                    analysis,
                )

            # 5. Verification
            effective_criteria = criteria or AcceptanceCriteria(
                min_length=1,
                model_judge_required=self.config.model_judge_verification,
            )

            v_result = await self.verifier.verify(
                candidate_output,
                effective_criteria,
                prompt=prompt,
                retries_attempted=0,
                max_retries=self.config.max_retries if self.config.auto_retry_verification else 0,
            )

            # 6. Retry (if failed and recommended)
            if not v_result.passed and v_result.retry_recommended and v_result.retry_prompt:
                logger.info(
                    "Output verification failed (%s). Triggering 1-turn retry.",
                    ", ".join(v_result.reasons),
                )
                if direct_retry_fn is not None and analysis.strategy == "direct":
                    retry_output = await direct_retry_fn(v_result.retry_prompt)
                else:
                    # Strong model revision
                    retry_provider = (
                        self.router.get_provider(ModelRole.STRONG)
                        if self.router is not None
                        else self.provider
                    )
                    req = ModelRequest(
                        system="Revise and fix the response to satisfy all verification requirements.",
                        messages=[
                            Message(role="user", content=prompt),
                            Message(role="assistant", content=candidate_output),
                            Message(role="user", content=v_result.retry_prompt),
                        ],
                    )
                    resp = await retry_provider.complete(req)
                    self.budget_manager.record_tokens(
                        resp.usage.get("prompt_tokens", 10),
                        resp.usage.get("completion_tokens", 10),
                    )
                    self.budget_manager.record_step(1)
                    retry_output = resp.text

                # Re-verify retry result (no more retries)
                v_result = await self.verifier.verify(
                    retry_output,
                    effective_criteria,
                    prompt=prompt,
                    retries_attempted=1,
                    max_retries=self.config.max_retries,
                )
                candidate_output = retry_output

            self.budget_manager.end_turn()

            if span.is_recording():
                span.set_attribute("yusufclaw.orchestration.strategy_used", analysis.strategy)
                span.set_attribute("yusufclaw.orchestration.verified", v_result.passed)
                if self.router is not None:
                    _, p_name, _ = self.router.route(role=ModelRole.DEFAULT, analysis=analysis)
                    span.set_attribute("yusufclaw.routing.provider", p_name)
                    span.set_attribute("yusufclaw.routing.role", ModelRole.DEFAULT.value)

            return candidate_output, analysis


__all__ = ["AdaptiveOrchestrator"]
