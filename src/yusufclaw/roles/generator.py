"""Capability matrix documentation generator for YusufClaw.

Generates `docs/CAPABILITY-MATRIX.md` with a structured markdown table and
architectural documentation comparing all specialist roles across tools, MCP servers,
model tiers, memory scopes, risk levels, approval gates, budgets, and sandbox requirements.
"""

from __future__ import annotations

from pathlib import Path

from yusufclaw.roles.loader import RoleCatalog, get_role_catalog


def generate_capability_matrix_markdown(catalog: RoleCatalog | None = None) -> str:
    """Generate the full markdown documentation for the capability matrix."""
    cat = catalog or get_role_catalog()
    roles = cat.list_roles()

    lines: list[str] = [
        "# YusufClaw Specialist Roles — Capability Matrix",
        "",
        "> Explicit capability matrices governing specialist agent roles, modeled on a capable personal assistant (like Grok Bot) but better.",
        "",
        "## 1. Overview and Security Principles",
        "",
        "Every specialist role in YusufClaw is constrained by a deterministic **Capability Matrix**:",
        "- **Least Privilege (Progent Monotonic Grants)**: Subagents receive the strict intersection of permissions required for their task.",
        "- **Explicit Tool & MCP Whitelisting**: Any tool or MCP server not explicitly granted is denied fail-closed.",
        "- **Memory Scope Isolation**: Specialist agents can only read and write within authorized scopes; isolation prevents cross-session pollution.",
        "- **External Channel Containment**: Agents are restricted in which channels they can reach. **Crucially: Any external send ALWAYS requires explicit human approval** (`send = always approval`).",
        "- **Rootless Sandbox Execution**: Code execution, git modifications, and cloud operations require rootless container/bubblewrap isolation.",
        "- **ActionCard Approval Gating**: Destructive actions, deployments, submissions, and financial transactions mandate cryptographic ActionCard approval.",
        "",
        "## 2. Capability Matrix Comparison Table",
        "",
        "| Role | Name | Tier | Risk | Sandbox | Allowed Tools | MCP Servers | Memory (R / W) | Gated Approvals | Send Channels |",
        "| :--- | :--- | :---: | :---: | :---: | :--- | :--- | :--- | :--- | :--- |",
    ]

    for role in roles:
        tools_str = ", ".join(f"`{t}`" for t in role.allowed_tools) if role.allowed_tools else "None"
        mcp_str = ", ".join(f"`{m}`" for m in role.allowed_mcp_servers) if role.allowed_mcp_servers else "None"
        mem_r = ", ".join(role.memory_scopes.read) if role.memory_scopes.read else "None"
        mem_w = ", ".join(role.memory_scopes.write) if role.memory_scopes.write else "None"
        mem_str = f"R: {mem_r}<br>W: {mem_w}"
        approvals_str = (
            ", ".join(f"`{a}`" for a in role.approval_actions)
            if role.approval_actions
            else "None"
        )
        sandbox_str = f"Yes ({role.sandbox_mode})" if role.sandbox_required else "No"
        channels_str = (
            ", ".join(f"`{c}`" for c in role.send_channels)
            if role.send_channels
            else "None (Never sends)"
        )

        lines.append(
            f"| **{role.role_id}** | {role.name} | `{role.model_tier}` | `{role.risk_level}` | {sandbox_str} | {tools_str} | {mcp_str} | {mem_str} | {approvals_str} | {channels_str} |"
        )

    lines.extend([
        "",
        "## 3. Specialist Role Profiles",
        "",
    ])

    for role in roles:
        lines.extend([
            f"### `{role.role_id}` — {role.name}",
            f"**Description**: {role.description}",
            "",
            f"- **Model Tier**: `{role.model_tier}`",
            f"- **Inherent Risk Level**: `{role.risk_level}`",
            f"- **Sandbox Isolation**: `{'Mandatory (' + role.sandbox_mode + ')' if role.sandbox_required else 'Not required'}`",
            f"- **Budget Ceilings**: Max `{role.budget_limits.max_tokens_per_turn}` tokens/turn, `${role.budget_limits.max_cost_per_turn:.2f}`/turn, `{role.budget_limits.max_steps_per_turn}` steps/turn.",
            f"- **Allowed Tools**: {', '.join(f'`{t}`' for t in role.allowed_tools) if role.allowed_tools else 'None'}",
            f"- **Allowed MCP Servers**: {', '.join(f'`{m}`' for m in role.allowed_mcp_servers) if role.allowed_mcp_servers else 'None'}",
            f"- **Equipped Skills**: {', '.join(f'`{s}`' for s in role.skills) if role.skills else 'None'}",
            f"- **Memory Read Scopes**: `{', '.join(role.memory_scopes.read)}`",
            f"- **Memory Write Scopes**: `{', '.join(role.memory_scopes.write) if role.memory_scopes.write else 'Read-only'}`",
            f"- **Approval-Gated Actions**: {', '.join(f'`{a}`' for a in role.approval_actions) if role.approval_actions else 'None'}",
            f"- **Permitted Send Channels**: {', '.join(f'`{c}`' for c in role.send_channels) if role.send_channels else 'None (Strictly prohibited from sending)'}",
            "",
            "#### A2A Discovery Card",
            "```json",
            role.to_a2a_card().model_dump_json(indent=2),
            "```",
            "",
        ])

    lines.extend([
        "## 4. Enforcement and Compliance Verification",
        "",
        "The capability matrix is enforced by `RolePolicyEnforcer` at the security layer before any tool execution:",
        "1. **Tool Verification**: If a tool is invoked that is not in `allowed_tools`, the policy engine immediately returns `PolicyDecision(kind='deny')`.",
        "2. **MCP Verification**: Unapproved MCP servers are blocked fail-closed.",
        "3. **Memory Isolation**: Write or read operations outside authorized scopes are denied.",
        "4. **Channel Egress**: Roles without send privileges cannot egress messages. For permitted roles, all outbound sends require interactive user confirmation.",
        "5. **High-Impact Actions**: All sensitive actions (e.g. deployments, form submissions, pushes) require human approval cards.",
        "",
    ])

    return "\n".join(lines)


def write_capability_matrix_docs(
    target_path: str | Path = "docs/CAPABILITY-MATRIX.md",
    catalog: RoleCatalog | None = None,
) -> Path:
    """Generate and write the capability matrix documentation to disk."""
    content = generate_capability_matrix_markdown(catalog)
    p = Path(target_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


if __name__ == "__main__":
    out_path = write_capability_matrix_docs()
    print(f"Generated capability matrix documentation at: {out_path}")
