"""Tools subpackage for YusufClaw.

Implements sandboxed tools for the agent:
- Shell tool: command execution inside isolated environments with approval prompts.
- Files tool: bounded filesystem read/write operations with path allowlists.
- Web fetch: HTTP client with SSRF protections and domain restrictions.
- Browser: Playwright-based web navigation and DOM extraction.
- Delegation: subagent spawning and external CLI coordination.
- Model Context Protocol (MCP) client and server integration.
"""

import httpx

from yusufclaw.tools.fetch import (
    BLOCKED_HOST_EXACT,
    DnsLookup,
    create_web_fetch_tool,
    is_blocked_hostname,
    is_blocked_ip,
    validate_host_ssrf,
)
from yusufclaw.tools.files import (
    DEFAULT_WORKSPACE_DIR,
    create_file_list_tool,
    create_file_read_tool,
    create_file_write_tool,
    is_restricted_secret_path,
    resolve_safe_path,
)
from yusufclaw.tools.manifest import GuardedTool
from yusufclaw.tools.shell import (
    DEFAULT_ALLOWED_COMMANDS,
    SAFE_READ_ONLY_BINARIES,
    contains_restricted_path,
    create_shell_tool,
    extract_command_binaries,
    is_dangerous_command,
)
from yusufclaw.tools.time import create_current_time_tool, current_time_tool


def create_default_tools(
    workspace_dir: str = DEFAULT_WORKSPACE_DIR,
    dns_lookup: DnsLookup | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[GuardedTool]:
    """Create standard suite of guarded tools scoped to workspace_dir."""
    return [
        create_current_time_tool(),
        create_shell_tool(workspace_dir=workspace_dir),
        create_file_read_tool(workspace_root=workspace_dir),
        create_file_write_tool(workspace_root=workspace_dir),
        create_file_list_tool(workspace_root=workspace_dir),
        create_web_fetch_tool(dns_lookup=dns_lookup, client=client),
    ]


__all__ = [
    "BLOCKED_HOST_EXACT",
    "DEFAULT_ALLOWED_COMMANDS",
    "DEFAULT_WORKSPACE_DIR",
    "DnsLookup",
    "GuardedTool",
    "SAFE_READ_ONLY_BINARIES",
    "contains_restricted_path",
    "create_current_time_tool",
    "create_default_tools",
    "create_file_list_tool",
    "create_file_read_tool",
    "create_file_write_tool",
    "create_shell_tool",
    "create_web_fetch_tool",
    "current_time_tool",
    "extract_command_binaries",
    "is_blocked_hostname",
    "is_blocked_ip",
    "is_dangerous_command",
    "is_restricted_secret_path",
    "resolve_safe_path",
    "validate_host_ssrf",
]
