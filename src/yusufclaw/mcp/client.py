"""Official MCP SDK client wrapper supporting stdio and streamable HTTP / SSE.

Connects to MCP servers, manages sessions, and invokes remote tools with timeouts.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import mcp.types as types
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from yusufclaw.mcp.models import MCPServerConfig


class MCPClientError(Exception):
    """Base exception for MCP client failures."""


class MCPClient:
    """Client for connecting to Model Context Protocol (MCP) servers."""

    def __init__(self, default_timeout: float = 30.0) -> None:
        self.default_timeout = default_timeout

    @asynccontextmanager
    async def session_context(
        self, config: MCPServerConfig
    ) -> AsyncGenerator[ClientSession, None]:
        """Establish an MCP ClientSession using the configured transport."""
        timeout = config.timeout_seconds or self.default_timeout

        if config.transport == "stdio":
            if not config.command:
                raise MCPClientError(f"Server '{config.name}' requires 'command' for stdio transport")
            params = StdioServerParameters(
                command=config.command,
                args=config.args,
                env=config.env or None,
                cwd=config.cwd or None,
            )
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(
                    read_stream, write_stream, read_timeout_seconds=timeout
                ) as session:
                    async with asyncio.timeout(timeout):
                        await session.initialize()
                    yield session

        elif config.transport == "streamable_http":
            if not config.url:
                raise MCPClientError(
                    f"Server '{config.name}' requires 'url' for streamable_http transport"
                )
            async with streamable_http_client(url=config.url) as (read_stream, write_stream):
                async with ClientSession(
                    read_stream, write_stream, read_timeout_seconds=timeout
                ) as session:
                    async with asyncio.timeout(timeout):
                        await session.initialize()
                    yield session

        elif config.transport == "sse":
            if not config.url:
                raise MCPClientError(f"Server '{config.name}' requires 'url' for sse transport")
            async with sse_client(url=config.url) as (read_stream, write_stream):
                async with ClientSession(
                    read_stream, write_stream, read_timeout_seconds=timeout
                ) as session:
                    async with asyncio.timeout(timeout):
                        await session.initialize()
                    yield session

        else:
            raise MCPClientError(f"Unsupported MCP transport: {config.transport}")

    async def list_tools(self, config: MCPServerConfig) -> list[types.Tool]:
        """Discover tools exposed by an MCP server."""
        timeout = config.timeout_seconds or self.default_timeout
        try:
            async with self.session_context(config) as session:
                async with asyncio.timeout(timeout):
                    result = await session.list_tools()
                    return list(result.tools)
        except TimeoutError as e:
            raise MCPClientError(
                f"Timeout discovering tools from MCP server '{config.name}' ({timeout}s)"
            ) from e
        except Exception as e:
            raise MCPClientError(
                f"Failed to discover tools from MCP server '{config.name}': {e}"
            ) from e

    async def call_tool(
        self,
        config: MCPServerConfig,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str:
        """Execute a tool on an MCP server and return output as string."""
        timeout = config.timeout_seconds or self.default_timeout
        try:
            async with self.session_context(config) as session:
                async with asyncio.timeout(timeout):
                    call_result = await session.call_tool(tool_name, arguments)

                parts: list[str] = []
                for item in call_result.content:
                    if hasattr(item, "text"):
                        parts.append(str(item.text))
                    elif isinstance(item, dict) and "text" in item:
                        parts.append(str(item["text"]))
                    else:
                        parts.append(str(item))

                output_str = "\n".join(parts)
                if call_result.is_error:
                    return f"MCP Tool Error: {output_str}"
                return output_str
        except TimeoutError as e:
            raise MCPClientError(
                f"Timeout executing tool '{tool_name}' on MCP server '{config.name}' ({timeout}s)"
            ) from e
        except Exception as e:
            raise MCPClientError(
                f"Error calling tool '{tool_name}' on MCP server '{config.name}': {e}"
            ) from e
