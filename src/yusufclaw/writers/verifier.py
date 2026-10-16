"""Parent verification hook for writer delegates.

Executes test suites (pytest or custom test command) inside the child worktree
workspace before the writer job can be reported as successful.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import sys
from pathlib import Path

from yusufclaw.writers.models import VerificationHookResult
from yusufclaw.writers.security import scrub_environment

logger = logging.getLogger(__name__)


class WriterVerifier:
    """Verifies that changes made in the child worktree pass acceptance test suites."""

    def __init__(self, default_test_command: list[str] | str | None = None) -> None:
        self.default_test_command = default_test_command

    def resolve_test_command(self, test_command: list[str] | str | None = None) -> list[str]:
        """Resolve command to execute. Defaults to running current virtualenv's pytest."""
        chosen = test_command if test_command is not None else self.default_test_command

        if chosen is None:
            # Run pytest using the current Python runtime
            return [sys.executable, "-m", "pytest", "-q"]

        if isinstance(chosen, str):
            return shlex.split(chosen)

        return list(chosen)

    async def verify_workspace(
        self,
        workspace_path: Path | str,
        *,
        test_command: list[str] | str | None = None,
        timeout: float = 60.0,
        env: dict[str, str] | None = None,
    ) -> VerificationHookResult:
        """Run verification command inside the isolated worktree directory."""
        path = Path(workspace_path).resolve()
        if not path.is_dir():
            return VerificationHookResult(
                passed=False,
                test_output=f"Verification failed: Workspace directory '{path}' does not exist.",
                exit_code=-1,
                command=[],
            )

        cmd = self.resolve_test_command(test_command)
        clean_env = scrub_environment(overrides=env)

        logger.info("Executing verification hook in %s: %s", path, " ".join(cmd))
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(path),
                env=clean_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            combined_output = f"{stdout}\n{stderr}".strip()
            exit_code = proc.returncode or 0
            passed = exit_code == 0

            return VerificationHookResult(
                passed=passed,
                test_output=combined_output,
                exit_code=exit_code,
                command=cmd,
            )

        except TimeoutError:
            try:
                proc.kill()
                await proc.communicate()
            except Exception:
                pass
            return VerificationHookResult(
                passed=False,
                test_output=f"Verification hook timed out after {timeout} seconds.",
                exit_code=-1,
                command=cmd,
            )
        except Exception as exc:
            logger.warning("Verification hook execution failed: %s", exc)
            return VerificationHookResult(
                passed=False,
                test_output=f"Verification hook execution error: {exc}",
                exit_code=-1,
                command=cmd,
            )
