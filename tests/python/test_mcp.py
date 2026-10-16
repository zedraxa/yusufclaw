"""Offline tests for YusufClaw MCP client, tool adaptation, pinning, and selection."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from yusufclaw.mcp.adapter import (
    QuarantinedServerError,
    discover_mcp_tools,
)
from yusufclaw.mcp.client import MCPClient
from yusufclaw.mcp.models import MCPConfig, MCPServerConfig
from yusufclaw.mcp.pinning import (
    PinningStore,
    compute_tools_hash,
)
from yusufclaw.mcp.selection import DynamicToolSelector
from yusufclaw.models.types import ToolDefinition
from yusufclaw.tools.manifest import GuardedTool

MOCK_SERVER_SCRIPT = """
import sys
from mcp.server.mcpserver import MCPServer

server = MCPServer("test-offline-mcp")

@server.tool()
def multiply(a: int, b: int) -> int:
    '''Multiply two numbers together'''
    return a * b

@server.tool()
def ping() -> str:
    '''Simple ping check'''
    return "pong"

if __name__ == "__main__":
    server.run("stdio")
"""


def test_mcp_config_serialization(tmp_path: Path) -> None:
    """Verify MCPConfig and MCPServerConfig parsing, loading, and saving."""
    data = {
        "servers": {
            "local_calc": {
                "transport": "stdio",
                "command": "python3",
                "args": ["-m", "calc"],
                "version": "1.2.0",
                "pinned_hash": "abc123hash",
                "timeout_seconds": 15.0,
            },
            "remote_api": {
                "transport": "streamable_http",
                "url": "http://localhost:8080/mcp",
                "headers": {"Authorization": "Bearer token"},
                "enabled": False,
            },
        },
        "default_timeout": 20.0,
    }

    cfg = MCPConfig.from_dict(data)
    assert len(cfg.servers) == 2
    assert cfg.servers["local_calc"].command == "python3"
    assert cfg.servers["local_calc"].version == "1.2.0"
    assert cfg.servers["remote_api"].transport == "streamable_http"
    assert not cfg.servers["remote_api"].enabled

    save_file = tmp_path / "mcp.json"
    cfg.save(save_file)
    assert save_file.exists()

    loaded = MCPConfig.from_file(save_file)
    assert len(loaded.servers) == 2
    assert loaded.servers["local_calc"].pinned_hash == "abc123hash"


def test_compute_tools_hash_determinism() -> None:
    """Verify compute_tools_hash produces identical hashes regardless of list ordering."""
    t1 = {
        "name": "read_data",
        "description": "Reads data from db",
        "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}},
    }
    t2 = {
        "name": "write_data",
        "description": "Writes data to db",
        "input_schema": {"type": "object", "properties": {"val": {"type": "number"}}},
    }

    hash_order_1 = compute_tools_hash([t1, t2])
    hash_order_2 = compute_tools_hash([t2, t1])
    assert hash_order_1 == hash_order_2

    # Modified description should change hash
    t1_modified = dict(t1)
    t1_modified["description"] = "Altered malicious description"
    hash_altered = compute_tools_hash([t1_modified, t2])
    assert hash_altered != hash_order_1


def test_pinning_store_and_quarantine(tmp_path: Path) -> None:
    """Verify pinning store handles new unpinned, verified, and tampered servers."""
    lock_file = tmp_path / "mcp.lock.json"
    store = PinningStore(lock_path=lock_file)

    server_name = "sqlite_srv"
    initial_hash = "deadbeef1234567890"

    # 1. Unpinned server -> unverified / quarantined
    is_safe, status, reason = store.check_server(server_name, initial_hash)
    assert not is_safe
    assert status == "unverified"
    assert "quarantined" in (reason or "").lower()

    # 2. Pin server -> active
    record = store.pin_server(server_name, initial_hash, version="1.0.0")
    assert record.status == "active"
    assert not store.is_quarantined(server_name)

    is_safe, status, reason = store.check_server(server_name, initial_hash)
    assert is_safe
    assert status == "active"

    # 3. Hash mismatch (poisoning attack) -> quarantine
    tampered_hash = "0000badc0de0000"
    is_safe_tampered, status_tampered, reason_tampered = store.check_server(
        server_name, tampered_hash
    )
    assert not is_safe_tampered
    assert status_tampered == "quarantined"
    assert "mismatch" in (reason_tampered or "")

    # 4. Explicit quarantine
    store.quarantine_server(server_name, reason="Security audit failure")
    assert store.is_quarantined(server_name)


@pytest.mark.asyncio
async def test_offline_mock_mcp_server(tmp_path: Path) -> None:
    """Test full offline stdio MCP discovery, pinning, quarantine, and execution."""
    server_script = tmp_path / "mock_mcp_server.py"
    server_script.write_text(MOCK_SERVER_SCRIPT, encoding="utf-8")

    server_cfg = MCPServerConfig(
        name="test_math",
        transport="stdio",
        command=sys.executable,
        args=[str(server_script)],
        timeout_seconds=10.0,
    )

    lock_path = tmp_path / "mcp.lock.json"
    store = PinningStore(lock_path=lock_path)
    client = MCPClient()

    # Phase 1: Unpinned discovery -> Server should be quarantined
    tools, quarantined = await discover_mcp_tools(
        servers=[server_cfg],
        client=client,
        pinning_store=store,
    )
    assert len(tools) == 0
    assert "test_math" in quarantined
    assert store.is_quarantined("test_math")

    # Phase 2: Compute real hash and pin the server
    raw_tools = await client.list_tools(server_cfg)
    assert len(raw_tools) == 2
    server_hash = compute_tools_hash(raw_tools)
    store.pin_server("test_math", server_hash, version="1.0.0")
    assert not store.is_quarantined("test_math")

    # Phase 3: Discover tools again -> Now verified and active
    active_tools, quarantined_2 = await discover_mcp_tools(
        servers=[server_cfg],
        client=client,
        pinning_store=store,
    )
    assert len(quarantined_2) == 0
    assert len(active_tools) == 2

    tool_names = {t.name for t in active_tools}
    assert "test_math_multiply" in tool_names
    assert "test_math_ping" in tool_names

    # Check manifest properties
    multiply_tool = next(t for t in active_tools if t.name == "test_math_multiply")
    assert isinstance(multiply_tool, GuardedTool)
    assert multiply_tool.manifest.source == "mcp"
    assert multiply_tool.manifest.effects == ["read_untrusted"]
    assert multiply_tool.manifest.description_sha256 is not None

    # Phase 4: Execute tool via GuardedTool
    result = await multiply_tool.execute({"a": 7, "b": 6})
    assert "42" in result

    # Phase 5: If quarantined, execution must be refused
    store.quarantine_server("test_math", reason="Revoked")
    with pytest.raises(QuarantinedServerError):
        await multiply_tool.execute({"a": 2, "b": 3})


def test_dynamic_tool_selection() -> None:
    """Test dynamic tool selection when tool count exceeds 30 tools."""
    # Create 35 dummy tools
    tools: list[ToolDefinition] = []

    # Essential core tools
    tools.append(
        ToolDefinition(name="current_time", description="Get current UTC time", input_schema={})
    )
    tools.append(
        ToolDefinition(
            name="file_read", description="Read file contents from workspace", input_schema={}
        )
    )

    # Database tools
    for i in range(10):
        tools.append(
            ToolDefinition(
                name=f"db_query_{i}",
                description=f"Execute SQL select query number {i} on database",
                input_schema={},
            )
        )

    # Weather tools
    for i in range(10):
        tools.append(
            ToolDefinition(
                name=f"weather_sensor_{i}",
                description=f"Retrieve temperature and weather forecast sensor metric {i}",
                input_schema={},
            )
        )

    # Math tools
    for i in range(13):
        tools.append(
            ToolDefinition(
                name=f"math_calc_{i}",
                description=f"Perform algebraic calculus number {i}",
                input_schema={},
            )
        )

    assert len(tools) == 35  # > 30 threshold

    selector = DynamicToolSelector(
        threshold=30,
        top_k=5,
        always_include={"current_time", "file_read"},
    )

    # Query for weather
    selected = selector.select(tools=tools, query="check tomorrow's weather forecast and sensor")

    selected_names = [t.name for t in selected]
    # Always included tools must be present
    assert "current_time" in selected_names
    assert "file_read" in selected_names

    # Weather tools should be ranked top
    weather_matches = [name for name in selected_names if "weather" in name]
    assert len(weather_matches) > 0

    # Total selected should be <= always_include (2) + top_k (5) = 7
    assert len(selected) <= 7

    # When tools <= threshold (e.g. 10 tools), all tools must be returned unchanged
    small_set = tools[:10]
    unfiltered = selector.select(tools=small_set, query="weather")
    assert len(unfiltered) == 10
