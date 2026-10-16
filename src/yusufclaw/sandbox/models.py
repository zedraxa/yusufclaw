"""Pydantic models and data structures for YusufClaw sandbox subsystem.

Defines schemas for:
- Resource and time limits (memory, cpu, timeout, network constraints).
- Sandbox execution results.
- Append-only reversible action log entries with undo hints.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SandboxConfig(BaseModel):
    """Configuration and resource boundaries for sandbox execution."""

    model_config = ConfigDict(populate_by_name=True)

    memory_limit_mb: int = Field(
        default=512, description="Maximum resident memory in megabytes"
    )
    cpu_limit: float = Field(
        default=1.0, description="CPU core quota (e.g. 1.0 = 1 full core)"
    )
    timeout_seconds: float = Field(
        default=30.0, description="Execution timeout limit in seconds"
    )
    max_output_bytes: int = Field(
        default=65536, description="Maximum captured stdout/stderr size in bytes (64 KB)"
    )
    allow_network: bool = Field(
        default=False, description="Whether network access is permitted (disabled by default)"
    )
    workspace_dir: str = Field(
        default=".data/workspace", description="Target workspace directory mounted for read/write"
    )
    read_only_binds: list[str] = Field(
        default_factory=list, description="Host paths to mount strictly read-only"
    )
    env: dict[str, str] = Field(
        default_factory=dict, description="Clean environment variables for the sandbox"
    )


class SandboxResult(BaseModel):
    """Result of executing a command or code inside a sandbox."""

    model_config = ConfigDict(populate_by_name=True)

    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    backend: str
    timed_out: bool = False

    @property
    def success(self) -> bool:
        """Return True if command exited with code 0 without timing out."""
        return self.exit_code == 0 and not self.timed_out


ActionStatus = Literal["executed", "undone", "failed"]


class ActionLogEntry(BaseModel):
    """Record in the append-only reversible action log tracking tool side-effects."""

    model_config = ConfigDict(populate_by_name=True)

    action_id: str
    timestamp: str
    tool_name: str
    action_type: str
    target: str
    details: dict[str, Any] = Field(default_factory=dict)
    undo_hint: dict[str, Any] | None = None
    reversible: bool = True
    status: ActionStatus = "executed"
    created_at: float = Field(default_factory=time.time)


class UndoResult(BaseModel):
    """Outcome of attempting an undo operation against an action log entry."""

    model_config = ConfigDict(populate_by_name=True)

    action_id: str
    success: bool
    message: str
