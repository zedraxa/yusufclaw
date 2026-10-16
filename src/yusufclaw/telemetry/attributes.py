"""OpenTelemetry attribute and span naming constants for YusufClaw.

Follows OpenTelemetry Semantic Conventions (GenAI incubating v1.30+)
and YusufClaw domain conventions for agent turns, tools, and security approvals.
"""

from typing import Final

# ---------------------------------------------------------------------------
# Span Names
# ---------------------------------------------------------------------------
SPAN_AGENT_TURN: Final[str] = "yusufclaw.agent.turn"
SPAN_MODEL_CALL: Final[str] = "yusufclaw.model.complete"
SPAN_TOOL_EXECUTION: Final[str] = "yusufclaw.tool.execute"
SPAN_APPROVAL_WAIT: Final[str] = "yusufclaw.approval.wait"

# ---------------------------------------------------------------------------
# GenAI Semantic Conventions (OpenTelemetry LLM / GenAI conventions)
# ---------------------------------------------------------------------------
ATTR_GEN_AI_SYSTEM: Final[str] = "gen_ai.system"
ATTR_GEN_AI_REQUEST_MODEL: Final[str] = "gen_ai.request.model"
ATTR_GEN_AI_RESPONSE_MODEL: Final[str] = "gen_ai.response.model"
ATTR_GEN_AI_OPERATION_NAME: Final[str] = "gen_ai.operation.name"
ATTR_GEN_AI_USAGE_PROMPT_TOKENS: Final[str] = "gen_ai.usage.prompt_tokens"
ATTR_GEN_AI_USAGE_COMPLETION_TOKENS: Final[str] = "gen_ai.usage.completion_tokens"
ATTR_GEN_AI_USAGE_TOTAL_TOKENS: Final[str] = "gen_ai.usage.total_tokens"
ATTR_GEN_AI_USAGE_INPUT_TOKENS: Final[str] = "gen_ai.usage.input_tokens"
ATTR_GEN_AI_USAGE_OUTPUT_TOKENS: Final[str] = "gen_ai.usage.output_tokens"
ATTR_GEN_AI_REQUEST_MAX_TOKENS: Final[str] = "gen_ai.request.max_tokens"
ATTR_GEN_AI_REQUEST_TEMPERATURE: Final[str] = "gen_ai.request.temperature"

# ---------------------------------------------------------------------------
# Agent Turn Attributes
# ---------------------------------------------------------------------------
ATTR_SESSION_ID: Final[str] = "yusufclaw.session_id"
ATTR_REQUEST_ID: Final[str] = "yusufclaw.request_id"
ATTR_STEP_COUNT: Final[str] = "yusufclaw.turn.step_count"
ATTR_TURN_STATUS: Final[str] = "yusufclaw.turn.status"

# ---------------------------------------------------------------------------
# Tool Execution Attributes
# ---------------------------------------------------------------------------
ATTR_TOOL_NAME: Final[str] = "yusufclaw.tool.name"
ATTR_TOOL_CALL_ID: Final[str] = "yusufclaw.tool.call_id"
ATTR_TOOL_STATUS: Final[str] = "yusufclaw.tool.status"
ATTR_TOOL_IS_GUARDED: Final[str] = "yusufclaw.tool.is_guarded"

# ---------------------------------------------------------------------------
# ActionCard Approval Attributes
# ---------------------------------------------------------------------------
ATTR_APPROVAL_ID: Final[str] = "yusufclaw.approval.id"
ATTR_APPROVAL_TOOL_NAME: Final[str] = "yusufclaw.approval.tool_name"
ATTR_APPROVAL_STATUS: Final[str] = "yusufclaw.approval.status"
