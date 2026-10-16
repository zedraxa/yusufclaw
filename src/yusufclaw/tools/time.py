"""Current time tool for YusufClaw."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from yusufclaw.security.models import ToolManifest
from yusufclaw.tools.manifest import GuardedTool


def create_current_time_tool() -> GuardedTool:
    """Create current_time read-only clock tool."""
    manifest = ToolManifest(
        id="current_time",
        version="1.0.0",
        source="builtin",
        effects=[],
    )

    async def execute(_input_args: dict[str, Any]) -> str:
        return datetime.now(UTC).isoformat()

    return GuardedTool(
        name="current_time",
        description="Return the current date and time in UTC.",
        input_schema={
            "type": "object",
            "properties": {},
        },
        manifest=manifest,
        execute_fn=execute,
        requires_approval=False,
    )


current_time_tool = create_current_time_tool()
