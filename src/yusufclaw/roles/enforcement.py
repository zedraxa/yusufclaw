"""Role-based capability enforcement engine.

Enforces explicit CapabilityMatrix constraints against incoming tool calls:
1. External channel delivery controls (unauthorized channels denied; send = always approval).
2. MCP server allowlist enforcement.
3. Memory read/write scope boundary enforcement.
4. High-risk and explicit approval action gating (ActionCard rendering).
5. Tool allowlist enforcement (fail-closed denial for tools outside matrix).
6. Monotonic integration with base deterministic security policy (decide).
"""

from __future__ import annotations

import logging
from typing import Any

from yusufclaw.roles.models import CapabilityMatrix
from yusufclaw.security import (
    Grants,
    PolicyDecision,
    SessionTaint,
    ToolManifest,
    Value,
    decide,
    render_card,
)
from yusufclaw.telemetry import get_tracer

logger = logging.getLogger(__name__)

# Tools that represent sending messages to external communication channels
CHANNEL_SEND_TOOLS = {
    "send_message",
    "mail_send",
    "telegram_send",
    "web_send",
    "channel_send",
}


class RolePolicyEnforcer:
    """Enforces capability matrix boundaries and approval requirements for specialist roles."""

    def __init__(self, default_role: CapabilityMatrix | None = None) -> None:
        self.default_role = default_role

    def enforce(
        self,
        role: CapabilityMatrix,
        manifest: ToolManifest,
        args: Value[dict[str, Any]],
        taint: SessionTaint,
    ) -> PolicyDecision:
        """Evaluate a tool invocation against the specialist role's capability matrix.

        Evaluation order (deterministic, fail-closed):
        1. Channel communication checks: Target channel must be allowed, and sending
           ALWAYS requires human approval.
        2. MCP server allowlist: If tool is an MCP tool, server must be permitted.
        3. Memory scope checks: Read/write memory boundaries must be honored.
        4. Role approval actions: Explicitly gated actions require human approval.
        5. Tool allowlist: Non-MCP tool must be explicitly permitted.
        6. Base security policy: Monotonic grants, secrecy, taint, untrusted inputs.
        """
        tracer = get_tracer("yusufclaw.roles")
        with tracer.start_as_current_span("yusufclaw.roles.enforce") as span:
            if span.is_recording():
                span.set_attribute("yusufclaw.role.id", role.role_id)
                span.set_attribute("yusufclaw.tool.id", manifest.id)

            # 1. External Channel Delivery Check (Send = Always Approval)
            is_channel_send = (
                manifest.id in CHANNEL_SEND_TOOLS
                or "send_message" in manifest.effects
                or "channel" in args.v
            )
            if is_channel_send:
                target_channel = str(
                    args.v.get("channel")
                    or ("email" if manifest.id == "mail_send" else "web")
                ).lower()

                # Verify role is permitted to target this channel
                if not role.can_send_to_channel(target_channel):
                    reason = (
                        f"role-capability: Role '{role.role_id}' is not permitted "
                        f"to send to channel '{target_channel}'"
                    )
                    logger.warning(reason)
                    return PolicyDecision(kind="deny", reason=reason)

                # Send = ALWAYS approval
                logger.info(
                    "Channel delivery requires human approval card for role '%s' to '%s'",
                    role.role_id,
                    target_channel,
                )
                return PolicyDecision(kind="card", card=render_card(manifest, args, taint))

            # 2. MCP Server Allowlist Check
            if manifest.source == "mcp":
                mcp_server = args.v.get("__mcp_server__") or args.v.get("server") or ""
                # Also check server prefix if tool is named server__tool
                if not mcp_server and "__" in manifest.id:
                    mcp_server = manifest.id.split("__", 1)[0]
                if not mcp_server or not role.can_use_mcp_server(str(mcp_server)):
                    reason = (
                        f"role-capability: MCP server '{mcp_server or manifest.id}' is not permitted "
                        f"for specialist role '{role.role_id}'"
                    )
                    logger.warning(reason)
                    return PolicyDecision(kind="deny", reason=reason)

            # 3. Memory Scope Check
            if manifest.id in ("recall", "memory_search", "search_memory"):
                target_scope = str(args.v.get("scope") or "session")
                if not role.can_read_memory(target_scope):
                    reason = (
                        f"role-capability: Role '{role.role_id}' is not authorized to "
                        f"read memory scope '{target_scope}'"
                    )
                    logger.warning(reason)
                    return PolicyDecision(kind="deny", reason=reason)

            if manifest.id in ("remember", "revise", "forget", "memory_write"):
                target_scope = str(args.v.get("scope") or "session")
                if not role.can_write_memory(target_scope):
                    reason = (
                        f"role-capability: Role '{role.role_id}' is not authorized to "
                        f"write to memory scope '{target_scope}'"
                    )
                    logger.warning(reason)
                    return PolicyDecision(kind="deny", reason=reason)

            # 4. Role Explicit Approval Actions Check
            # Check tool ID or specific action parameters
            action_name = str(args.v.get("action") or manifest.id)
            if role.requires_approval(manifest.id) or role.requires_approval(action_name):
                logger.info(
                    "Action '%s' mandates human approval card for role '%s'",
                    action_name,
                    role.role_id,
                )
                return PolicyDecision(kind="card", card=render_card(manifest, args, taint))

            # 5. Tool Allowlist Check (for non-MCP tools)
            if manifest.source != "mcp":
                if not role.can_use_tool(manifest.id):
                    reason = (
                        f"role-capability: Tool '{manifest.id}' is not permitted "
                        f"for specialist role '{role.role_id}'"
                    )
                    logger.warning(reason)
                    return PolicyDecision(kind="deny", reason=reason)

            # 6. Base Deterministic Security Policy Delegation
            # Grants are monotonically constrained to the role's allowed tools
            allowed_set = set(role.allowed_tools)
            if manifest.source == "mcp":
                allowed_set.add(manifest.id)
            grants = Grants(allowed_tools=allowed_set)
            return decide(manifest, args, taint, grants)


def decide_with_role(
    role: CapabilityMatrix,
    manifest: ToolManifest,
    args: Value[dict[str, Any]],
    taint: SessionTaint,
) -> PolicyDecision:
    """Convenience helper to evaluate a tool call against a role's capability matrix."""
    enforcer = RolePolicyEnforcer()
    return enforcer.enforce(role, manifest, args, taint)


__all__ = [
    "RolePolicyEnforcer",
    "decide_with_role",
]
