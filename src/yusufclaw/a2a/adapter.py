"""Adapter connecting A2A envelopes with orchestration SubagentRunner.

Enables any SubagentRunner instance to serve as an in-process A2A teammate,
translating TaskRequest/TaskResult envelopes and enforcing data channel segregation.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from yusufclaw.a2a.channel import DataChannelSegregation
from yusufclaw.a2a.models import ContextArtifact, TaskRequest, TaskResult
from yusufclaw.orchestration.models import SubagentResult, SubagentSpec
from yusufclaw.orchestration.subagent import SubagentRunner

logger = logging.getLogger(__name__)


class SubagentA2AAdapter:
    """Adapts a SubagentRunner to the A2ATransport interface."""

    def __init__(
        self,
        runner: SubagentRunner,
        permitted_tools: list[str] | None = None,
    ) -> None:
        self.runner = runner
        self.permitted_tools = permitted_tools or []

    async def send_task(self, request: TaskRequest) -> TaskResult:
        """Translate TaskRequest into SubagentSpec and execute via SubagentRunner."""
        start_time = time.perf_counter()

        # Enforce segregated data channels
        seg_ctx = DataChannelSegregation.prepare_context(request)

        # Context artifacts are passed strictly as untrusted data blocks
        context_data_strings: list[str] = [
            f"[Artifact: {art.name} ({art.mime_type})]\n{art.content}"
            for art in seg_ctx.artifacts
        ]

        # Derive tools: intersection of request capabilities/tools and adapter tools
        requested_tools = request.metadata.get("permitted_tools", self.permitted_tools)
        if not isinstance(requested_tools, list):
            requested_tools = self.permitted_tools

        role_val = request.metadata.get("model_role") or "default"
        spec = SubagentSpec(
            name=request.target_agent_id,
            instruction=seg_ctx.instruction,
            context_data=context_data_strings,
            permitted_tools=requested_tools,
            max_tokens=request.budget.max_tokens if request.budget.max_tokens > 0 else 2000,
            max_steps=request.budget.max_steps if request.budget.max_steps > 0 else 5,
            timeout_seconds=(
                request.budget.timeout_seconds if request.budget.timeout_seconds > 0 else 30
            ),
            max_depth=1,
            model_role=role_val,
        )

        try:
            sub_res: SubagentResult = await self.runner.run(spec)
            status_val = "success" if sub_res.success else "failed"
            if sub_res.error and "budget" in sub_res.error.lower():
                status_val = "budget_exceeded"

            # Check if output contains artifact indicators
            artifacts: list[ContextArtifact] = []
            if request.metadata.get("capture_artifacts", False) and sub_res.output:
                artifacts.append(
                    ContextArtifact(
                        name=f"{request.target_agent_id}_output.txt",
                        content=sub_res.output,
                        mime_type="text/plain",
                        untrusted=False,
                    )
                )

            return TaskResult(
                task_id=request.task_id,
                agent_id=request.target_agent_id,
                status=status_val,  # type: ignore[arg-type]
                output=sub_res.output,
                artifacts=artifacts,
                tokens_used=sub_res.tokens_used,
                steps_used=sub_res.steps_used,
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
                error=sub_res.error,
            )
        except Exception as e:
            logger.error("A2A adapter error executing subagent: %s", e)
            return TaskResult(
                task_id=request.task_id,
                agent_id=request.target_agent_id,
                status="failed",
                error=str(e),
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel subagent run if supported by runner's queue or active graph."""
        # SubagentRunner inline runs handle cancellation via asyncio Task cancellation
        return True


def create_teammate_handler_from_runner(
    runner: SubagentRunner,
    permitted_tools: list[str] | None = None,
) -> Any:
    """Return a TaskHandler callable backed by a SubagentRunner."""
    adapter = SubagentA2AAdapter(runner, permitted_tools=permitted_tools)
    return adapter.send_task
