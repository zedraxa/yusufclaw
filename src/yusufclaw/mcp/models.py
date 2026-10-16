"""Pydantic models and configurations for YusufClaw MCP subsystem.

Defines schemas for:
- Server configuration (stdio and streamable HTTP / SSE transports).
- Server version and tool description SHA-256 hash pinning.
- Quarantine state representation for new or tampered MCP servers.
- Global MCP configuration file format.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TransportType = Literal["stdio", "streamable_http", "sse"]
QuarantineStatus = Literal["active", "quarantined", "unverified"]


class MCPServerConfig(BaseModel):
    """Configuration for an individual Model Context Protocol (MCP) server."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Unique identifier for the server")
    transport: TransportType = Field(
        default="stdio", description="Transport mechanism: stdio, streamable_http, or sse"
    )
    command: str | None = Field(
        default=None, description="Executable command for stdio transport"
    )
    args: list[str] = Field(
        default_factory=list, description="Command arguments for stdio transport"
    )
    env: dict[str, str] = Field(
        default_factory=dict, description="Environment variables passed to stdio server"
    )
    cwd: str | None = Field(
        default=None, description="Working directory for stdio server execution"
    )
    url: str | None = Field(
        default=None, description="Endpoint URL for streamable_http or sse transport"
    )
    headers: dict[str, str] = Field(
        default_factory=dict, description="HTTP headers for remote transports"
    )
    version: str | None = Field(
        default=None, description="Expected / pinned version string of the server"
    )
    pinned_hash: str | None = Field(
        default=None,
        description="Expected SHA-256 digest of canonical tool definitions",
    )
    quarantined: bool = Field(
        default=False,
        description="Whether server is quarantined and prevented from tool execution",
    )
    enabled: bool = Field(
        default=True, description="Whether this server is enabled for tool discovery"
    )
    timeout_seconds: float = Field(
        default=30.0, description="Per-request execution timeout in seconds"
    )


class ServerPinRecord(BaseModel):
    """Audit and lock record tracking server version, content hash, and quarantine status."""

    model_config = ConfigDict(populate_by_name=True)

    server_name: str
    version: str | None = None
    tools_hash: str
    status: QuarantineStatus = "active"
    quarantine_reason: str | None = None
    updated_at: str


class MCPConfig(BaseModel):
    """Top-level configuration holding all configured MCP servers."""

    model_config = ConfigDict(populate_by_name=True)

    servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
    default_timeout: float = 30.0
    pin_file: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MCPConfig:
        """Create MCPConfig from a dictionary (e.g. parsed mcp.json)."""
        raw_servers = data.get("servers", {})
        servers: dict[str, MCPServerConfig] = {}
        for name, s_data in raw_servers.items():
            if isinstance(s_data, dict):
                cfg_dict = dict(s_data)
                if "name" not in cfg_dict:
                    cfg_dict["name"] = name
                servers[name] = MCPServerConfig.model_validate(cfg_dict)
        return cls(
            servers=servers,
            default_timeout=float(data.get("default_timeout", 30.0)),
            pin_file=data.get("pin_file"),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> MCPConfig:
        """Load MCPConfig from a JSON file."""
        file_path = Path(path)
        if not file_path.exists():
            return cls()
        content = file_path.read_text(encoding="utf-8")
        raw = json.loads(content)
        if isinstance(raw, dict):
            return cls.from_dict(raw)
        return cls()

    def to_dict(self) -> dict[str, Any]:
        """Serialize configuration to a dictionary."""
        return {
            "servers": {k: v.model_dump(exclude_none=True) for k, v in self.servers.items()},
            "default_timeout": self.default_timeout,
            "pin_file": self.pin_file,
        }

    def save(self, path: str | Path) -> None:
        """Save configuration to a JSON file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
