"""Isolated git worktree workspace management for writer delegates.

Handles creation, isolation, git status change detection, and teardown of
worktrees under a configurable parent directory.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from yusufclaw.writers.security import validate_workspace_path

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE_PARENT = Path(
    os.environ.get("YUSUFCLAW_WRITERS_WORKSPACE_DIR", "/tmp/yusufclaw-worktrees")
)


class WorkspaceConflictError(RuntimeError):
    """Raised when worktree workspace creation encounters an unresolvable collision or conflict."""


@dataclass
class WorktreeWorkspace:
    """Represents an active isolated git worktree workspace."""

    job_id: str
    workspace_path: Path
    repo_path: Path
    base_ref: str
    branch_name: str | None
    is_detached: bool
    active: bool = True


class WorkspaceManager:
    """Manages the lifecycle of isolated git worktrees for child writer jobs."""

    def __init__(self, parent_dir: Path | str | None = None) -> None:
        self.parent_dir = Path(parent_dir).resolve() if parent_dir else DEFAULT_WORKSPACE_PARENT.resolve()

    def _ensure_parent_dir(self) -> None:
        """Create parent directory with secure user-only permissions if it does not exist."""
        if not self.parent_dir.exists():
            self.parent_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    async def _run_git(
        self,
        args: list[str],
        cwd: Path | str,
        timeout: float = 30.0,
    ) -> tuple[int, str, str]:
        """Execute a git command within specified cwd and return (exit_code, stdout, stderr)."""
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return (
                proc.returncode or 0,
                stdout_bytes.decode("utf-8", errors="replace"),
                stderr_bytes.decode("utf-8", errors="replace"),
            )
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            return (-1, "", f"Git command 'git {' '.join(args)}' timed out after {timeout}s.")

    async def get_repo_root(self, search_from: Path | str | None = None) -> Path:
        """Resolve current git repository top-level directory."""
        search_path = Path(search_from).resolve() if search_from else Path.cwd().resolve()
        code, stdout, stderr = await self._run_git(["rev-parse", "--show-toplevel"], cwd=search_path)
        if code != 0:
            raise RuntimeError(f"Unable to find git repository root from {search_path}: {stderr.strip()}")
        return Path(stdout.strip()).resolve()

    async def create_worktree(
        self,
        job_id: str,
        base_ref: str = "HEAD",
        *,
        repo_path: Path | str | None = None,
        branch_name: str | None = None,
    ) -> WorktreeWorkspace:
        """Create a new isolated git worktree workspace for the job.

        If branch_name is provided, a new branch is created. Otherwise, creates a detached worktree.
        """
        self._ensure_parent_dir()
        root = await self.get_repo_root(repo_path)
        workspace_dir = self.parent_dir / f"ws-{job_id}"

        # Validate path confinement
        validate_workspace_path(workspace_dir, self.parent_dir)

        if workspace_dir.exists():
            raise WorkspaceConflictError(
                f"Workspace conflict: Destination directory already exists at '{workspace_dir}'"
            )

        cmd: list[str] = ["worktree", "add"]
        is_detached = branch_name is None

        if branch_name:
            cmd.extend(["-b", branch_name, str(workspace_dir), base_ref])
        else:
            cmd.extend(["--detach", str(workspace_dir), base_ref])

        exit_code, stdout, stderr = await self._run_git(cmd, cwd=root)
        if exit_code != 0:
            err_lower = stderr.lower()
            if "already exists" in err_lower or "conflict" in err_lower or "is already checked out" in err_lower:
                raise WorkspaceConflictError(
                    f"Git worktree conflict for job '{job_id}' on ref '{base_ref}': {stderr.strip()}"
                )
            raise RuntimeError(
                f"Failed to create git worktree at '{workspace_dir}': {stderr.strip()}"
            )

        logger.info("Created isolated worktree for job %s at %s", job_id, workspace_dir)
        return WorktreeWorkspace(
            job_id=job_id,
            workspace_path=workspace_dir,
            repo_path=root,
            base_ref=base_ref,
            branch_name=branch_name,
            is_detached=is_detached,
            active=True,
        )

    async def get_changed_paths(self, workspace: WorktreeWorkspace) -> list[str]:
        """Detect all files created, modified, or deleted in the worktree workspace."""
        if not workspace.workspace_path.exists():
            return []

        changed: set[str] = set()

        # 1. Uncommitted changes (working tree & index) via git status --porcelain
        code, status_out, _ = await self._run_git(
            ["status", "--porcelain", "-uall"], cwd=workspace.workspace_path
        )
        if code == 0:
            for line in status_out.splitlines():
                line = line.strip()
                if not line or len(line) < 3:
                    continue
                # Format: XY <path> or XY <path> -> <path> (rename)
                path_part = line[2:].strip()
                if " -> " in path_part:
                    path_part = path_part.split(" -> ")[-1].strip()
                # Strip quotes if git quoted special names
                path_part = path_part.strip('"')
                if path_part:
                    changed.add(path_part)

        # 2. Committed changes relative to base_ref
        code, diff_out, _ = await self._run_git(
            ["diff", "--name-only", workspace.base_ref], cwd=workspace.workspace_path
        )
        if code == 0:
            for line in diff_out.splitlines():
                path_part = line.strip().strip('"')
                if path_part:
                    changed.add(path_part)

        return sorted(changed)

    async def cleanup_worktree(
        self,
        workspace: WorktreeWorkspace,
        *,
        delete_branch: bool = True,
    ) -> None:
        """Remove the isolated worktree and delete its temporary branch if applicable."""
        if not workspace.active and not workspace.workspace_path.exists():
            return

        # 1. Remove git worktree via git worktree remove --force
        if workspace.workspace_path.exists():
            await self._run_git(
                ["worktree", "remove", "--force", str(workspace.workspace_path)],
                cwd=workspace.repo_path,
            )

        # 2. Prune worktrees in case of lingering reference
        await self._run_git(["worktree", "prune"], cwd=workspace.repo_path)

        # 3. Delete branch if one was created
        if delete_branch and workspace.branch_name:
            await self._run_git(
                ["branch", "-D", workspace.branch_name],
                cwd=workspace.repo_path,
            )

        # 4. Fallback cleanup of filesystem if directory remains
        if workspace.workspace_path.exists():
            try:
                shutil.rmtree(workspace.workspace_path, ignore_errors=True)
            except Exception as exc:
                logger.warning("Worktree directory cleanup warning: %s", exc)

        workspace.active = False
        logger.info("Cleaned up worktree for job %s at %s", workspace.job_id, workspace.workspace_path)

    @asynccontextmanager
    async def isolated_workspace(
        self,
        job_id: str,
        base_ref: str = "HEAD",
        *,
        repo_path: Path | str | None = None,
        branch_name: str | None = None,
        auto_cleanup: bool = True,
    ) -> AsyncIterator[WorktreeWorkspace]:
        """Context manager creating and automatically cleaning up an isolated worktree."""
        workspace = await self.create_worktree(
            job_id=job_id,
            base_ref=base_ref,
            repo_path=repo_path,
            branch_name=branch_name,
        )
        try:
            yield workspace
        finally:
            if auto_cleanup:
                await self.cleanup_worktree(workspace)
