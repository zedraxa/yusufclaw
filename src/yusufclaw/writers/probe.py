"""Protocol probe for identified external coding CLIs (agy, codex).

Checks CLI availability, executable path, and protocol/version compatibility
prior to spawning isolated writer child jobs.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path

from yusufclaw.writers.models import ProtocolProbeResult

logger = logging.getLogger(__name__)

# Semver and version extraction pattern
VERSION_REGEX = re.compile(r"(\d+\.\d+(?:\.\d+)?(?:[-.][a-zA-Z0-9_.-]+)?)")


class CLIProtocolProbe:
    """Probes host system for external coding CLIs and verifies protocol support."""

    KNOWN_CLIS: frozenset[str] = frozenset({"agy", "codex"})

    def __init__(self, executable_overrides: dict[str, str] | None = None) -> None:
        self.executable_overrides = executable_overrides or {}

    def resolve_executable(self, cli_name_or_path: str) -> str | None:
        """Resolve full executable path from name or override."""
        if cli_name_or_path in self.executable_overrides:
            candidate = self.executable_overrides[cli_name_or_path]
            return candidate if Path(candidate).is_file() else None

        # Check if direct file path provided
        direct_path = Path(cli_name_or_path)
        if direct_path.is_file() and os_is_executable(direct_path):
            return str(direct_path.resolve())

        # Look up in PATH
        return shutil.which(cli_name_or_path)

    async def probe(
        self,
        cli_name_or_path: str,
        *,
        timeout: float = 5.0,
        version_flag: str = "--version",
    ) -> ProtocolProbeResult:
        """Execute protocol probe against the target CLI binary.

        Runs '<cli> --version' and extracts the reported version.
        """
        resolved = self.resolve_executable(cli_name_or_path)
        if resolved is None:
            return ProtocolProbeResult(
                cli=cli_name_or_path,
                available=False,
                error=f"Executable for CLI '{cli_name_or_path}' not found on host or PATH.",
                probe_command=[cli_name_or_path, version_flag],
            )

        cmd = [resolved, version_flag]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
            stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
            stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                err_msg = stderr or stdout or f"Exited with code {proc.returncode}"
                return ProtocolProbeResult(
                    cli=cli_name_or_path,
                    available=False,
                    executable_path=resolved,
                    error=f"Version check failed: {err_msg}",
                    probe_command=cmd,
                )

            combined_out = f"{stdout}\n{stderr}".strip()
            version = self._parse_version(combined_out)

            return ProtocolProbeResult(
                cli=cli_name_or_path,
                available=True,
                version=version,
                executable_path=resolved,
                probe_command=cmd,
            )

        except TimeoutError:
            return ProtocolProbeResult(
                cli=cli_name_or_path,
                available=False,
                executable_path=resolved,
                error=f"Probe timed out after {timeout} seconds.",
                probe_command=cmd,
            )
        except Exception as exc:
            logger.warning("Failed to probe CLI %s: %s", cli_name_or_path, exc)
            return ProtocolProbeResult(
                cli=cli_name_or_path,
                available=False,
                executable_path=resolved,
                error=f"Probe execution error: {exc}",
                probe_command=cmd,
            )

    @staticmethod
    def _parse_version(output: str) -> str:
        """Extract version identifier from probe output."""
        match = VERSION_REGEX.search(output)
        if match:
            return match.group(1)
        # If regex doesn't match standard semver, take first line up to 64 chars
        first_line = output.splitlines()[0] if output else "unknown"
        return first_line[:64].strip()


def os_is_executable(path: Path) -> bool:
    """Check if file has executable bit set on POSIX systems."""
    import os

    return os.access(path, os.X_OK)


async def probe_cli(
    cli_name_or_path: str,
    *,
    timeout: float = 5.0,
    version_flag: str = "--version",
) -> ProtocolProbeResult:
    """Convenience helper to probe a CLI protocol status."""
    probe = CLIProtocolProbe()
    return await probe.probe(cli_name_or_path, timeout=timeout, version_flag=version_flag)
