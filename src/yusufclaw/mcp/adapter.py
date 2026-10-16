"""Adapts MCP tools into YusufClaw GuardedTool security manifests.

Ensures every discovered MCP tool:
- Is registered as a GuardedTool with a deterministic ToolManifest (source='mcp', effects=['read_untrusted']).
- Verifies server pinning and quarantine status before execution.
- Evaluates against the deterministic security engine and Human-in-the-Loop ActionCards.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

import mcp.types as types

from yusufclaw.mcp.client import MCPClient
from yusufclaw.mcp.models import MCPConfig, MCPServerConfig
from yusufclaw.mcp.pinning import PinningStore, compute_tools_hash
from yusufclaw.security.models import ToolManifest
from yusufclaw.tools.manifest import GuardedTool


class QuarantinedServerError(Exception):
    """Raised when an attempt is made to invoke an MCP tool on a quarantined server."""


def adapt_mcp_tool(
    tool: types.Tool,
    server_config: MCPServerConfig,
    client: MCPClient,
    pinning_store: PinningStore | None = None,
) -> GuardedTool:
    """Adapt a single MCP tool into a YusufClaw GuardedTool with security policies."""
    tool_id = f"{server_config.name}_{tool.name}"
    description = tool.description or f"MCP tool {tool.name} from server {server_config.name}"
    schema = tool.input_schema if isinstance(tool.input_schema, dict) else {}

    desc_sha256 = hashlib.sha256(description.encode("utf-8")).hexdigest()

    # ToolManifest source='mcp' and effects=['read_untrusted'] (RESEARCH-ASIST YC-16)
    manifest = ToolManifest(
        id=tool_id,
        version=server_config.version or "1.0.0",
        source="mcp",
        effects=["read_untrusted"],
        description_sha256=desc_sha256,
    )

    async def execute_mcp_tool(args: dict[str, Any]) -> str:
        # 1. Verify quarantine status
        if server_config.quarantined:
            raise QuarantinedServerError(
                f"Cannot execute tool '{tool.name}': Server '{server_config.name}' is quarantined"
            )
        if pinning_store and pinning_store.is_quarantined(server_config.name):
            rec = pinning_store.get_record(server_config.name)
            reason = rec.quarantine_reason if rec else "Quarantined"
            raise QuarantinedServerError(
                f"Cannot execute tool '{tool.name}': Server '{server_config.name}' is in quarantine ({reason})"
            )

        # 2. Call remote MCP server via official SDK
        return await client.call_tool(
            config=server_config,
            tool_name=tool.name,
            arguments=args,
        )

    def check_approval(args: dict[str, Any]) -> bool:
        # Require approval if server is explicitly flagged or in quarantine
        if server_config.quarantined:
            return True
        if pinning_store and pinning_store.is_quarantined(server_config.name):
            return True
        return False

    return GuardedTool(
        name=tool_id,
        description=description,
        input_schema=schema,
        manifest=manifest,
        execute_fn=execute_mcp_tool,
        requires_approval=check_approval,
    )


async def discover_mcp_tools(
    servers: Sequence[MCPServerConfig] | MCPConfig,
    client: MCPClient | None = None,
    pinning_store: PinningStore | None = None,
) -> tuple[list[GuardedTool], dict[str, str]]:
    """Discover tools across configured MCP servers, applying pinning and quarantine checks.

    Returns:
        (active_guarded_tools, quarantined_server_reasons)
    """
    mcp_client = client or MCPClient()
    store = pinning_store or PinningStore()

    server_list: list[MCPServerConfig]
    if isinstance(servers, MCPConfig):
        server_list = list(servers.servers.values())
    else:
        server_list = list(servers)

    active_tools: list[GuardedTool] = []
    quarantined: dict[str, str] = {}

    for cfg in server_list:
        if not cfg.enabled:
            continue

        try:
            raw_tools = await mcp_client.list_tools(cfg)
        except Exception as e:
            quarantined[cfg.name] = f"Connection/Discovery failure: {e}"
            continue

        # Compute hash of discovered tools
        current_hash = compute_tools_hash(raw_tools)

        # Evaluate against pinning store
        is_safe, status, reason = store.check_server(
            server_name=cfg.name,
            current_hash=current_hash,
            declared_version=cfg.version,
            config_pinned_hash=cfg.pinned_hash,
        )

        if not is_safe:
            # Server is unpinned or tampered: put into quarantine
            quarantine_reason = reason or f"Server state: {status}"
            store.quarantine_server(
                server_name=cfg.name,
                reason=quarantine_reason,
                tools_hash=current_hash,
                version=cfg.version,
            )
            quarantined[cfg.name] = quarantine_reason
            continue

        # Server is active and verified: adapt tools
        for raw_tool in raw_tools:
            guarded = adapt_mcp_tool(
                tool=raw_tool,
                server_config=cfg,
                client=mcp_client,
                pinning_store=store,
            )
            active_tools.append(guarded)

    return active_tools, quarantined
