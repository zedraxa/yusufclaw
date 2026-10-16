"""Writer delegate runner and execution adapter (P2-01 / P2-02).

Coordinates protocol probing, isolated git worktree workspaces, environment
scrubbing, child budget caps, concurrency limits (max 2, depth 1), parent verification,
and optional queue-backed durable execution.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from typing import Any

from yusufclaw.queue import Job, JobQueue, Worker, default_queue, queue_enabled
from yusufclaw.queue.worker import JobFailed
from yusufclaw.writers.models import (
    ProtocolProbeResult,
    WriterResult,
    WriterSpec,
)
from yusufclaw.writers.probe import CLIProtocolProbe
from yusufclaw.writers.security import scrub_environment
from yusufclaw.writers.verifier import WriterVerifier
from yusufclaw.writers.workspace import (
    WorkspaceConflictError,
    WorkspaceManager,
    WorktreeWorkspace,
)

logger = logging.getLogger(__name__)


class WriterDelegateRunner:
    """Manages execution of bounded child writer delegates (agy, codex)."""

    MAX_CONCURRENT_CHILDREN: int = 2
    MAX_DEPTH: int = 1

    def __init__(
        self,
        *,
        job_queue: JobQueue | None = None,
        durable: bool | None = None,
        probe: CLIProtocolProbe | None = None,
        verifier: WriterVerifier | None = None,
        workspace_manager: WorkspaceManager | None = None,
    ) -> None:
        self.job_queue = job_queue
        self.durable = queue_enabled() if durable is None else durable
        self.probe = probe or CLIProtocolProbe()
        self.verifier = verifier or WriterVerifier()
        self.workspace_manager = workspace_manager or WorkspaceManager()
        self._concurrency_semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_CHILDREN)
        self._active_children: int = 0
        self._lock = asyncio.Lock()

    @property
    def active_children_count(self) -> int:
        """Return the number of currently executing child writer jobs."""
        return self._active_children

    def _build_cli_command(self, resolved_path: str, spec: WriterSpec) -> list[str]:
        """Construct process command arguments based on identified CLI conventions."""
        cli_name = os.path.basename(resolved_path).lower()
        if "agy" in cli_name:
            cmd = [resolved_path, "-p", spec.prompt, "--dangerously-skip-permissions"]
            if spec.cli_args:
                cmd.extend(spec.cli_args)
            return cmd
        if "codex" in cli_name:
            cmd = [resolved_path, "exec", "--prompt", spec.prompt]
            if spec.cli_args:
                cmd.extend(spec.cli_args)
            return cmd

        # Custom or generic binary
        cmd = [resolved_path]
        if spec.cli_args:
            cmd.extend(spec.cli_args)
        cmd.append(spec.prompt)
        return cmd

    async def handle_queue_job(self, job: Job) -> dict[str, Any]:
        """Worker handler for queue-backed writer delegate execution."""
        spec_dict = job.payload.get("spec", {})
        spec = WriterSpec.model_validate(spec_dict)
        result = await self._run_bounded_child(spec)
        if not result.success:
            raise JobFailed(result.model_dump())
        return result.model_dump()

    async def run(self, spec: WriterSpec) -> WriterResult:
        """Run a writer delegate job either via durable queue or inline subprocess."""
        # 1. Enforce depth constraint (depth 1 only)
        if spec.depth > self.MAX_DEPTH:
            return WriterResult(
                job_id=spec.job_id,
                cli=spec.cli,
                success=False,
                exit_code=-1,
                error=f"Depth limit exceeded: writer delegates must be depth <= {self.MAX_DEPTH} (received {spec.depth}).",
                depth=spec.depth,
            )

        if not self.durable:
            return await self._run_bounded_child(spec)

        # Queue-backed execution path
        store = self.job_queue or default_queue()
        job: Job | None = None
        try:
            await store.initialize()
            job = await store.enqueue(
                "writer",
                {"spec": spec.model_dump()},
                id=spec.job_id,
                user_id=spec.user_id,
                role="writer",
                parent_id=spec.parent_job_id,
                timeout=spec.budget.max_time_seconds + 60.0,
                max_attempts=1,
                concurrency_key="writer_delegate",
            )

            worker = Worker(store, {"writer": self.handle_queue_job})
            while True:
                await worker.run_once(job.id)
                current = await store.get(job.id)
                if current.state == "succeeded":
                    return WriterResult.model_validate(current.result or {})
                if current.state in {"failed", "cancelled"}:
                    if current.result is not None:
                        res = WriterResult.model_validate(current.result)
                        if current.state == "cancelled":
                            res.cancelled = True
                        return res
                    return WriterResult(
                        job_id=spec.job_id,
                        cli=spec.cli,
                        success=False,
                        exit_code=-1,
                        error=current.error or current.state,
                        cancelled=current.state == "cancelled",
                        depth=spec.depth,
                    )
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            if job is not None:
                await store.cancel(job.id)
            raise
        finally:
            if self.job_queue is None:
                await store.close()

    async def _run_bounded_child(self, spec: WriterSpec) -> WriterResult:
        """Execute a single writer child process bounded by concurrency, budget, and verification."""
        # 1. Concurrency gate (at most two concurrent children)
        async with self._concurrency_semaphore:
            async with self._lock:
                self._active_children += 1
            try:
                return await self._execute_child_pipeline(spec)
            finally:
                async with self._lock:
                    self._active_children = max(0, self._active_children - 1)

    async def _execute_child_pipeline(self, spec: WriterSpec) -> WriterResult:
        start_time = time.monotonic()

        # 2. Protocol probe
        probe_result: ProtocolProbeResult = await self.probe.probe(spec.cli)
        if not probe_result.available or not probe_result.executable_path:
            return WriterResult(
                job_id=spec.job_id,
                cli=spec.cli,
                success=False,
                exit_code=-1,
                error=f"Protocol probe failed for '{spec.cli}': {probe_result.error or 'Executable unavailable'}",
                depth=spec.depth,
            )

        # 3. Workspace setup
        ws_mgr = (
            WorkspaceManager(parent_dir=spec.workspace_parent_dir)
            if spec.workspace_parent_dir
            else self.workspace_manager
        )
        workspace: WorktreeWorkspace | None = None
        try:
            workspace = await ws_mgr.create_worktree(
                job_id=spec.job_id,
                base_ref=spec.base_ref,
                branch_name=spec.branch_name,
            )
        except WorkspaceConflictError as exc:
            logger.warning("Workspace conflict for job %s: %s", spec.job_id, exc)
            return WriterResult(
                job_id=spec.job_id,
                cli=spec.cli,
                success=False,
                exit_code=-1,
                conflict=True,
                error=f"Workspace conflict: {exc}",
                depth=spec.depth,
            )
        except Exception as exc:
            logger.error("Failed to create worktree workspace: %s", exc)
            return WriterResult(
                job_id=spec.job_id,
                cli=spec.cli,
                success=False,
                exit_code=-1,
                error=f"Workspace creation error: {exc}",
                depth=spec.depth,
            )

        # 4. Process execution inside child worktree
        cmd = self._build_cli_command(probe_result.executable_path, spec)
        clean_env = scrub_environment(overrides=spec.env_overrides)
        timed_out = False
        cancelled = False
        stdout_text = ""
        stderr_text = ""
        exit_code = -1

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(workspace.workspace_path),
                env=clean_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,  # Isolate in own process group for clean killing
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=spec.budget.max_time_seconds,
                )
                max_bytes = spec.budget.max_output_bytes
                stdout_text = stdout_bytes[:max_bytes].decode("utf-8", errors="replace")
                stderr_text = stderr_bytes[:max_bytes].decode("utf-8", errors="replace")
                exit_code = proc.returncode or 0

            except TimeoutError:
                timed_out = True
                logger.warning("Child writer %s timed out after %ss", spec.job_id, spec.budget.max_time_seconds)
                self._kill_process_group(proc)
                exit_code = -1

            except asyncio.CancelledError:
                cancelled = True
                logger.info("Child writer %s cancelled; terminating process group", spec.job_id)
                self._kill_process_group(proc)
                exit_code = -1
                raise

            # 5. Changed paths detection
            changed_paths: list[str] = []
            if workspace and workspace.workspace_path.exists():
                try:
                    changed_paths = await ws_mgr.get_changed_paths(workspace)
                except Exception as exc:
                    logger.warning("Failed to collect changed paths: %s", exc)

            duration = time.monotonic() - start_time

            # Check changed files budget
            budget_error: str | None = None
            if len(changed_paths) > spec.budget.max_changed_files:
                budget_error = (
                    f"Child changed {len(changed_paths)} files, exceeding limit of {spec.budget.max_changed_files}."
                )

            # 6. Parent verification hook
            test_passed: bool | None = None
            test_output: str | None = None
            verification_error: str | None = None

            if not timed_out and not cancelled and exit_code == 0 and not budget_error:
                ver_res = await self.verifier.verify_workspace(
                    workspace.workspace_path,
                    test_command=spec.test_command,
                    timeout=spec.budget.max_time_seconds,
                )
                test_passed = ver_res.passed
                test_output = ver_res.test_output
                if not ver_res.passed:
                    verification_error = (
                        f"Parent verification hook failed (exit code {ver_res.exit_code}):\n{ver_res.test_output[:500]}"
                    )

            # Determine overall success: process exited 0 AND parent verification passed
            overall_success = (
                not timed_out
                and not cancelled
                and exit_code == 0
                and not budget_error
                and (test_passed is True)
            )

            error_msg: str | None = None
            if timed_out:
                error_msg = f"Child writer job timed out after {spec.budget.max_time_seconds}s"
            elif cancelled:
                error_msg = "Child writer job was cancelled"
            elif exit_code != 0:
                error_msg = f"CLI process exited with non-zero code {exit_code}: {stderr_text[:300]}"
            elif budget_error:
                error_msg = budget_error
            elif verification_error:
                error_msg = verification_error

            summary = self._generate_summary(
                spec=spec,
                success=overall_success,
                changed_paths=changed_paths,
                duration=duration,
                error=error_msg,
            )

            return WriterResult(
                job_id=spec.job_id,
                cli=spec.cli,
                success=overall_success,
                exit_code=exit_code,
                summary=summary,
                changed_paths=changed_paths,
                stdout=stdout_text,
                stderr=stderr_text,
                duration_seconds=duration,
                workspace_path=str(workspace.workspace_path),
                test_passed=test_passed,
                test_output=test_output,
                error=error_msg,
                timed_out=timed_out,
                cancelled=cancelled,
                depth=spec.depth,
            )

        finally:
            # 7. Cleanup
            if spec.auto_cleanup and workspace:
                try:
                    await ws_mgr.cleanup_worktree(workspace)
                except Exception as exc:
                    logger.warning("Cleanup error for workspace %s: %s", workspace.job_id, exc)

    @staticmethod
    def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
        """Safely terminate a subprocess and its entire process group."""
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass

    @staticmethod
    def _generate_summary(
        spec: WriterSpec,
        success: bool,
        changed_paths: list[str],
        duration: float,
        error: str | None = None,
    ) -> str:
        """Create a human-readable summary of the writer delegate run."""
        status_word = "Completed successfully" if success else "Failed"
        parts = [
            f"Writer Delegate [{spec.cli}] {status_word} in {duration:.1f}s.",
            f"Prompt: {spec.prompt[:80]}...",
            f"Changed paths ({len(changed_paths)}): {', '.join(changed_paths[:5])}"
            + (f" and {len(changed_paths) - 5} more" if len(changed_paths) > 5 else ""),
        ]
        if error:
            parts.append(f"Error: {error}")
        return " | ".join(parts)


async def execute_writer(
    prompt: str,
    cli: str = "agy",
    *,
    test_command: list[str] | str | None = None,
    budget: Any = None,
    user_id: str = "local",
    parent_job_id: str | None = None,
    auto_cleanup: bool = True,
    runner: WriterDelegateRunner | None = None,
) -> WriterResult:
    """Convenience functional API to execute a bounded writer delegate."""
    active_runner = runner or WriterDelegateRunner()
    spec = WriterSpec(
        prompt=prompt,
        cli=cli,
        test_command=test_command,
        user_id=user_id,
        parent_job_id=parent_job_id,
        auto_cleanup=auto_cleanup,
    )
    if budget is not None:
        spec.budget = budget
    return await active_runner.run(spec)
