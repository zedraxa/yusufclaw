"""MCP (Model Context Protocol) package for YusufClaw.

Integrates official MCP Python SDK transports (stdio, streamable HTTP, SSE),
server configuration and tool discovery, security manifest adaptation,
server version/hash pinning, quarantine enforcement, and dynamic tool selection.
"""

from yusufclaw.mcp.adapter import (
    QuarantinedServerError,
    adapt_mcp_tool,
    discover_mcp_tools,
)
from yusufclaw.mcp.client import MCPClient, MCPClientError
from yusufclaw.mcp.models import (
    MCPConfig,
    MCPServerConfig,
    QuarantineStatus,
    ServerPinRecord,
    TransportType,
)
from yusufclaw.mcp.pinning import (
    PinningStore,
    canonical_tool_representation,
    compute_tools_hash,
)
from yusufclaw.mcp.selection import (
    DynamicToolSelector,
    select_tools_for_query,
)

__all__ = [
    "DynamicToolSelector",
    "MCPClient",
    "MCPClientError",
    "MCPConfig",
    "MCPServerConfig",
    "PinningStore",
    "QuarantineStatus",
    "QuarantinedServerError",
    "ServerPinRecord",
    "TransportType",
    "adapt_mcp_tool",
    "canonical_tool_representation",
    "compute_tools_hash",
    "discover_mcp_tools",
    "select_tools_for_query",
]
