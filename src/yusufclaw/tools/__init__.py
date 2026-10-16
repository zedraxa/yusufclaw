"""Tools subpackage for YusufClaw.

Implements sandboxed tools for the agent:
- Shell tool: command execution inside isolated environments with approval prompts.
- Files tool: bounded filesystem read/write operations with path allowlists.
- Web fetch: HTTP client with SSRF protections and domain restrictions.
- Browser: Playwright-based web navigation and DOM extraction.
- Delegation: subagent spawning and external CLI coordination.
- Model Context Protocol (MCP) client and server integration.

Note: Logic will be ported from TypeScript packages/tools and tests.
"""

__all__: list[str] = []
