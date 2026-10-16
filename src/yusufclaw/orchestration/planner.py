"""Plan-and-execute and delegation workflow using LangGraph.

Implements structured planning and sequential/delegated execution:
1. Planner node: Decomposes complex goals into an ExecutionPlan of distinct PlanSteps.
2. Step executor node: Executes steps sequentially or delegates to SubagentRunner.
3. Step verification node: Validates step outputs with Verifier and supports 1 retry.
4. Synthesizer node: Combines step results into a cohesive final response.
5. Budget awareness: Checks ceilings at every step and performs graceful stops.
6. OpenTelemetry tracing for planning and execution phases.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from yusufclaw.models.types import (
    Message,
    ModelProvider,
    ModelRequest,
    ToolDefinition,
)
from yusufclaw.orchestration.budget import BudgetManager
from yusufclaw.orchestration.models import (
    AcceptanceCriteria,
    ExecutionPlan,
    PlanStep,
    SubagentSpec,
    TaskAnalysis,
)
from yusufclaw.orchestration.subagent import SubagentRunner
from yusufclaw.orchestration.verifier import Verifier
from yusufclaw.telemetry import get_tracer

logger = logging.getLogger(__name__)


class PlanExecuteState(TypedDict):
    """Execution state for the plan-and-execute LangGraph workflow."""

    goal: str
    plan: ExecutionPlan
    current_step_index: int
    step_results: dict[int, str]
    final_response: str
    budget_exceeded: bool
    error: str | None


class PlanAndExecuteWorkflow:
    """Orchestrates plan generation, step delegation, verification, and synthesis."""

    def __init__(
        self,
        provider: ModelProvider,
        subagent_runner: SubagentRunner,
        budget_manager: BudgetManager,
        verifier: Verifier | None = None,
        available_tools: Sequence[ToolDefinition] | None = None,
    ) -> None:
        self.provider = provider
        self.subagent_runner = subagent_runner
        self.budget_manager = budget_manager
        self.verifier = verifier or Verifier(provider=provider)
        self.available_tools = list(available_tools) if available_tools else []
        self.available_tool_names = [t.name for t in self.available_tools]
        self.graph = self._build_graph()

    def _build_plan_from_analysis(self, goal: str, analysis: TaskAnalysis | None) -> ExecutionPlan:
        """Construct initial plan steps from task analysis subtasks or heuristics."""
        if analysis and analysis.subtasks:
            steps: list[PlanStep] = []
            for idx, st in enumerate(analysis.subtasks, 1):
                steps.append(
                    PlanStep(
                        step_id=idx,
                        description=st,
                        tool_or_subagent="subagent" if analysis.delegate_required else None,
                    )
                )
            return ExecutionPlan(goal=goal, steps=steps)

        # Fallback heuristic steps
        steps = [
            PlanStep(step_id=1, description=f"Analyze and gather necessary data for: {goal}"),
            PlanStep(step_id=2, description=f"Execute core actions and solve: {goal}"),
            PlanStep(step_id=3, description=f"Consolidate and verify outcomes for: {goal}"),
        ]
        return ExecutionPlan(goal=goal, steps=steps)

    async def _generate_model_plan(self, goal: str) -> ExecutionPlan:
        """Use the model to create a structured JSON execution plan."""
        system_instruction = (
            "You are the planning engine for YusufClaw. Decompose the user's goal into "
            "a sequential plan of 2 to 4 clear steps.\n"
            "Available tools: " + ", ".join(self.available_tool_names or ["none"]) + "\n"
            "Return JSON strictly in this format:\n"
            "{\n"
            '  "steps": [\n'
            '    {"step_id": 1, "description": "step description", "tool_or_subagent": "optional"}\n'
            "  ]\n"
            "}"
        )

        req = ModelRequest(
            system=system_instruction,
            messages=[Message(role="user", content=f"Create a plan for: {goal}")],
            temperature=0.0,
        )

        try:
            resp = await self.provider.complete(req)
            self.budget_manager.record_tokens(
                resp.usage.get("prompt_tokens", 10),
                resp.usage.get("completion_tokens", 10),
            )
            self.budget_manager.record_step(1)

            raw = resp.text.strip()
            if "```" in raw:
                match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
                if match:
                    raw = match.group(1).strip()

            parsed = json.loads(raw)
            raw_steps = parsed.get("steps", [])
            plan_steps = [
                PlanStep(
                    step_id=int(s.get("step_id", idx)),
                    description=str(s.get("description", "")),
                    tool_or_subagent=s.get("tool_or_subagent"),
                )
                for idx, s in enumerate(raw_steps, 1)
                if s.get("description")
            ]
            if plan_steps:
                return ExecutionPlan(goal=goal, steps=plan_steps)
        except Exception as exc:
            logger.warning("Model planning failed, using heuristic: %s", exc)

        return self._build_plan_from_analysis(goal, None)

    def _build_graph(self) -> Any:
        workflow = StateGraph(PlanExecuteState)

        async def planner_node(state: PlanExecuteState) -> dict[str, Any]:
            tracer = get_tracer("yusufclaw.orchestration")
            with tracer.start_as_current_span("yusufclaw.orchestration.plan_generation") as span:
                goal = state["goal"]
                plan = state.get("plan")
                if not plan or not plan.steps:
                    plan = await self._generate_model_plan(goal)

                if span.is_recording():
                    span.set_attribute("yusufclaw.plan.steps_count", len(plan.steps))

                return {
                    "plan": plan,
                    "current_step_index": 0,
                    "step_results": {},
                    "budget_exceeded": False,
                }

        async def execute_step_node(state: PlanExecuteState) -> dict[str, Any]:
            tracer = get_tracer("yusufclaw.orchestration")
            with tracer.start_as_current_span("yusufclaw.orchestration.plan_step") as span:
                plan = state["plan"]
                curr_idx = state["current_step_index"]
                results = dict(state.get("step_results", {}))

                if curr_idx >= len(plan.steps):
                    return {"current_step_index": curr_idx}

                step = plan.steps[curr_idx]
                step.status = "in_progress"

                if span.is_recording():
                    span.set_attribute("yusufclaw.step.id", step.step_id)
                    span.set_attribute("yusufclaw.step.description", step.description)

                # Check budget limits before execution
                status = self.budget_manager.check_limits()
                if status.is_exceeded:
                    step.status = "failed"
                    step.error = f"Budget limit reached: {status.exceeded_reason}"
                    return {
                        "plan": plan,
                        "budget_exceeded": True,
                        "current_step_index": len(plan.steps),
                    }

                step_output = ""
                # If delegated or step requests subagent
                if step.tool_or_subagent in ("subagent", "delegate") or "subagent" in step.description.lower():
                    sub_spec = SubagentSpec(
                        name=f"worker_step_{step.step_id}",
                        instruction=step.description,
                        permitted_tools=self.available_tool_names,
                        max_steps=4,
                    )
                    sub_res = await self.subagent_runner.run(sub_spec, budget_manager=self.budget_manager)
                    if sub_res.success:
                        step_output = sub_res.output
                    else:
                        step_output = f"Subagent error: {sub_res.error}"
                else:
                    # Direct step execution via model
                    req = ModelRequest(
                        system="You are executing a discrete step of an overall task plan. Provide factual, concise output.",
                        messages=[
                            Message(role="user", content=f"Goal: {plan.goal}\nCurrent Step: {step.description}")
                        ],
                    )
                    resp = await self.provider.complete(req)
                    self.budget_manager.record_tokens(
                        resp.usage.get("prompt_tokens", 10),
                        resp.usage.get("completion_tokens", 10),
                    )
                    self.budget_manager.record_step(1)
                    step_output = resp.text

                # Verify step output
                v_res = await self.verifier.verify(
                    step_output,
                    AcceptanceCriteria(min_length=1),
                    prompt=step.description,
                )

                if not v_res.passed and v_res.retry_recommended and v_res.retry_prompt:
                    # Execute 1 retry
                    req_retry = ModelRequest(
                        system="Revise the step execution based on feedback.",
                        messages=[
                            Message(role="user", content=step.description),
                            Message(role="assistant", content=step_output),
                            Message(role="user", content=v_res.retry_prompt),
                        ],
                    )
                    resp_retry = await self.provider.complete(req_retry)
                    self.budget_manager.record_tokens(
                        resp_retry.usage.get("prompt_tokens", 10),
                        resp_retry.usage.get("completion_tokens", 10),
                    )
                    self.budget_manager.record_step(1)
                    step_output = resp_retry.text

                step.status = "completed"
                step.result = step_output
                results[step.step_id] = step_output

                return {
                    "plan": plan,
                    "current_step_index": curr_idx + 1,
                    "step_results": results,
                }

        def route_steps(state: PlanExecuteState) -> str:
            if state.get("budget_exceeded", False):
                return "synthesizer"
            plan = state.get("plan")
            if not plan:
                return "synthesizer"
            if state.get("current_step_index", 0) < len(plan.steps):
                return "execute_step"
            return "synthesizer"

        async def synthesizer_node(state: PlanExecuteState) -> dict[str, Any]:
            tracer = get_tracer("yusufclaw.orchestration")
            with tracer.start_as_current_span("yusufclaw.orchestration.synthesis") as span:
                goal = state["goal"]
                results = state.get("step_results", {})
                budget_exceeded = state.get("budget_exceeded", False)

                summary_bullets = "\n".join(
                    f"- Step {sid}: {res}" for sid, res in sorted(results.items())
                )

                req = ModelRequest(
                    system=(
                        "You are the synthesis engine for YusufClaw. Synthesize the completed step "
                        "findings into a single coherent, thorough response to the user's overall goal."
                    ),
                    messages=[
                        Message(
                            role="user",
                            content=(
                                f"Overall Goal:\n{goal}\n\n"
                                f"Completed Step Results:\n{summary_bullets}\n\n"
                                "Provide a comprehensive final answer."
                            ),
                        )
                    ],
                )
                resp = await self.provider.complete(req)
                self.budget_manager.record_tokens(
                    resp.usage.get("prompt_tokens", 10),
                    resp.usage.get("completion_tokens", 10),
                )
                self.budget_manager.record_step(1)

                final_text = resp.text
                if budget_exceeded:
                    final_text += (
                        "\n\n[Notice: Budget limits were reached during execution. "
                        "Above response synthesized available partial progress.]"
                    )

                if span.is_recording():
                    span.set_attribute("yusufclaw.synthesis.steps_used", len(results))
                    span.set_attribute("yusufclaw.synthesis.budget_exceeded", budget_exceeded)

                return {"final_response": final_text}

        workflow.add_node("planner", planner_node)
        workflow.add_node("execute_step", execute_step_node)
        workflow.add_node("synthesizer", synthesizer_node)

        workflow.add_edge(START, "planner")
        workflow.add_edge("planner", "execute_step")
        workflow.add_conditional_edges("execute_step", route_steps, ["execute_step", "synthesizer"])
        workflow.add_edge("synthesizer", END)

        return workflow.compile()

    async def execute(
        self,
        goal: str,
        initial_plan: ExecutionPlan | None = None,
    ) -> str:
        """Run the full plan-and-execute workflow and return synthesized output."""
        tracer = get_tracer("yusufclaw.orchestration")
        with tracer.start_as_current_span("yusufclaw.orchestration.plan_workflow") as span:
            if span.is_recording():
                span.set_attribute("yusufclaw.workflow.goal", goal)

            initial_state: PlanExecuteState = {
                "goal": goal,
                "plan": initial_plan or ExecutionPlan(goal=goal),
                "current_step_index": 0,
                "step_results": {},
                "final_response": "",
                "budget_exceeded": False,
                "error": None,
            }

            final_state = await self.graph.ainvoke(initial_state)
            return str(final_state.get("final_response", ""))


__all__ = ["PlanAndExecuteWorkflow", "PlanExecuteState"]
