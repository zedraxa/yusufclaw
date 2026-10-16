"""Data models and schemas for YusufClaw writer delegates (P2-01).

Defines specifications, child budgets, verification outcomes, and structured
results for external coding CLI delegates (agy, codex).
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class ChildBudget(BaseModel):
    """Budget and resource constraints for a writer delegate child job."""

    max_time_seconds: float = Field(
        default=120.0,
        gt=0,
        description="Maximum execution wall-clock time in seconds before termination.",
    )
    max_output_bytes: int = Field(
        default=1_000_000,
        gt=0,
        description="Maximum bytes of combined stdout/stderr retained to prevent unbounded memory growth.",
    )
    max_changed_files: int = Field(
        default=100,
        gt=0,
        description="Maximum number of changed file paths permitted from a single child run.",
    )
    max_tokens: int | None = Field(
        default=None,
        description="Optional token budget cap for model accounting.",
    )


class WriterSpec(BaseModel):
    """Specification for invoking an external coding CLI delegate."""

    job_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex[:12],
        description="Unique identifier for the writer job.",
    )
    cli: str = Field(
        default="agy",
        description="Target coding CLI identifier ('agy', 'codex') or path to binary.",
    )
    prompt: str = Field(
        description="Natural language instruction / brief passed to the coding CLI.",
    )
    base_ref: str = Field(
        default="HEAD",
        description="Base git commit, tag, or branch from which to branch the isolated worktree.",
    )
    user_id: str = Field(
        default="local",
        description="Owner user identifier for queue scoping and access control.",
    )
    parent_job_id: str | None = Field(
        default=None,
        description="Optional parent queue job ID for hierarchical job tracking.",
    )
    depth: int = Field(
        default=1,
        ge=1,
        le=1,
        description="Delegation depth. Strictly bounded to 1 (children cannot spawn delegates).",
    )
    budget: ChildBudget = Field(
        default_factory=ChildBudget,
        description="Resource and time limits applied to the delegate execution.",
    )
    test_command: list[str] | str | None = Field(
        default=None,
        description="Verification command to run in child worktree (e.g. 'pytest -q'). Defaults to pytest.",
    )
    workspace_parent_dir: str | None = Field(
        default=None,
        description="Optional override for the parent directory containing isolated worktrees.",
    )
    env_overrides: dict[str, str] = Field(
        default_factory=dict,
        description="Explicit environment overrides to pass through scrubbing into the child process.",
    )
    cli_args: list[str] = Field(
        default_factory=list,
        description="Additional arguments forwarded to the CLI binary.",
    )
    auto_cleanup: bool = Field(
        default=True,
        description="Whether to automatically delete the worktree on job completion.",
    )
    branch_name: str | None = Field(
        default=None,
        description="Optional explicit git branch name; if None, detached worktree is created.",
    )


class ProtocolProbeResult(BaseModel):
    """Result of querying and verifying an external CLI's protocol capabilities."""

    cli: str = Field(description="Identified CLI name or path probed.")
    available: bool = Field(description="Whether the CLI executable was found and runnable.")
    version: str | None = Field(
        default=None,
        description="Detected semver or raw version string from the CLI.",
    )
    executable_path: str | None = Field(
        default=None,
        description="Absolute path to the resolved executable on host.",
    )
    error: str | None = Field(
        default=None,
        description="Failure details if probe failed or version mismatch occurred.",
    )
    probe_command: list[str] = Field(
        default_factory=list,
        description="The exact command executed during probe.",
    )


class VerificationHookResult(BaseModel):
    """Result of running parent verification tests inside the child worktree."""

    passed: bool = Field(description="Whether verification test suite passed (exit code 0).")
    test_output: str = Field(default="", description="Captured test output from stdout and stderr.")
    exit_code: int = Field(default=0, description="Process exit code from test command.")
    command: list[str] = Field(default_factory=list, description="Command used for verification.")


class WriterResult(BaseModel):
    """Structured result returned by a writer delegate execution."""

    job_id: str = Field(description="Unique identifier of the executed writer job.")
    cli: str = Field(description="Coding CLI executed ('agy', 'codex', or path).")
    success: bool = Field(
        description="True only if CLI finished cleanly AND parent verification passed.",
    )
    exit_code: int = Field(description="Exit code of the child CLI process.")
    summary: str = Field(default="", description="High-level summary of work performed.")
    changed_paths: list[str] = Field(
        default_factory=list,
        description="Relative paths of files created, modified, or deleted in the worktree.",
    )
    stdout: str = Field(default="", description="Standard output captured from CLI process.")
    stderr: str = Field(default="", description="Standard error captured from CLI process.")
    duration_seconds: float = Field(
        default=0.0,
        description="Total duration of the execution in seconds.",
    )
    workspace_path: str = Field(
        default="",
        description="Absolute path to the worktree where work was performed.",
    )
    test_passed: bool | None = Field(
        default=None,
        description="Outcome of parent verification hook (None if not executed).",
    )
    test_output: str | None = Field(
        default=None,
        description="Raw output from parent verification hook execution.",
    )
    error: str | None = Field(
        default=None,
        description="Error message if execution failed, timed out, or verification rejected.",
    )
    conflict: bool = Field(
        default=False,
        description="True if workspace or branch collided or git conflicts arose.",
    )
    timed_out: bool = Field(
        default=False,
        description="True if child process exceeded allocated budget time.",
    )
    cancelled: bool = Field(
        default=False,
        description="True if job was cancelled by parent or user.",
    )
    depth: int = Field(
        default=1,
        description="Delegation depth of this job (always 1).",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary additional execution metadata.",
    )
