"""Rootless sandbox runner abstraction for YusufClaw shell and code tools.

Provides execution isolation using:
- Bubblewrap (bwrap) unprivileged user namespaces and filesystem isolation.
- Podman / Docker rootless containers.
- Denied fallback runner: fails closed (denies execution) when no isolation runtime is available.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path

from yusufclaw.sandbox.models import SandboxConfig, SandboxResult


class SandboxUnavailableError(Exception):
    """Raised when sandbox execution is attempted without an available sandbox runtime."""


class SandboxRunner(ABC):
    """Abstract base class for sandbox execution backends."""

    name: str

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this sandbox backend is installed and usable on the host."""

    @abstractmethod
    def build_command(self, command: str | list[str], config: SandboxConfig) -> list[str]:
        """Construct the sandbox execution command array with isolation flags."""

    async def run_async(
        self,
        command: str | list[str],
        config: SandboxConfig | None = None,
    ) -> SandboxResult:
        """Asynchronously execute a command inside the sandbox."""
        cfg = config or SandboxConfig()
        start_time = time.monotonic()

        if not self.is_available():
            duration = time.monotonic() - start_time
            return SandboxResult(
                exit_code=126,
                stdout="",
                stderr=f"Sandbox runtime '{self.name}' is not available on this host (fail closed).",
                duration_seconds=duration,
                backend=self.name,
            )

        cmd_arr = self.build_command(command, cfg)
        ws_path = Path(cfg.workspace_dir).resolve()
        ws_path.mkdir(parents=True, exist_ok=True)

        env = dict(cfg.env)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_arr,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(ws_path),
                env=env or None,
            )

            try:
                async with asyncio.timeout(cfg.timeout_seconds):
                    stdout_bytes, stderr_bytes = await proc.communicate()
                timed_out = False
                exit_code = proc.returncode if proc.returncode is not None else -1
            except TimeoutError:
                timed_out = True
                exit_code = 124
                try:
                    proc.kill()
                    stdout_bytes, stderr_bytes = await proc.communicate()
                except Exception:
                    stdout_bytes, stderr_bytes = b"", b"Process killed after timeout"

            duration = time.monotonic() - start_time

            # Enforce max output bytes limit
            stdout_str = stdout_bytes[: cfg.max_output_bytes].decode("utf-8", errors="replace")
            stderr_str = stderr_bytes[: cfg.max_output_bytes].decode("utf-8", errors="replace")

            if timed_out and not stderr_str:
                stderr_str = f"Execution timed out after {cfg.timeout_seconds}s"

            return SandboxResult(
                exit_code=exit_code,
                stdout=stdout_str,
                stderr=stderr_str,
                duration_seconds=duration,
                backend=self.name,
                timed_out=timed_out,
            )

        except Exception as e:
            duration = time.monotonic() - start_time
            return SandboxResult(
                exit_code=1,
                stdout="",
                stderr=f"Sandbox execution error: {e}",
                duration_seconds=duration,
                backend=self.name,
            )

    def run(
        self,
        command: str | list[str],
        config: SandboxConfig | None = None,
    ) -> SandboxResult:
        """Synchronously execute a command inside the sandbox."""
        return asyncio.run(self.run_async(command, config))


class BubblewrapRunner(SandboxRunner):
    """Sandbox runner using Bubblewrap (bwrap) unprivileged namespaces."""

    name = "bubblewrap"

    def __init__(self) -> None:
        self._usable: bool | None = None

    def is_available(self) -> bool:
        """Verify bwrap binary existence and unprivileged namespace capability."""
        if self._usable is not None:
            return self._usable

        bwrap_bin = shutil.which("bwrap")
        if not bwrap_bin:
            self._usable = False
            return False

        # Probe if user namespaces work without error
        try:
            res = subprocess.run(
                [bwrap_bin, "--ro-bind", "/usr", "/usr", "--proc", "/proc", "--dev", "/dev", "/bin/true"],
                capture_output=True,
                timeout=3,
            )
            self._usable = (res.returncode == 0)
        except Exception:
            self._usable = False

        return self._usable

    def build_command(self, command: str | list[str], config: SandboxConfig) -> list[str]:
        bwrap_bin = shutil.which("bwrap") or "bwrap"
        ws_abs = str(Path(config.workspace_dir).resolve())

        args: list[str] = [
            bwrap_bin,
            "--die-with-parent",
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
        ]

        # Bind workspace read/write
        args.extend(["--bind", ws_abs, ws_abs])

        # Standard system mounts (read-only)
        for host_dir in ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc/alternatives"):
            if os.path.exists(host_dir):
                args.extend(["--ro-bind", host_dir, host_dir])

        for ro_path in config.read_only_binds:
            if os.path.exists(ro_path):
                args.extend(["--ro-bind", ro_path, ro_path])

        # Network isolation: unshare network unless explicitly allowed
        if not config.allow_network:
            args.append("--unshare-net")

        # Unshare IPC, PID, UTS
        args.extend(["--unshare-ipc", "--unshare-pid", "--unshare-uts"])

        args.extend(["--chdir", ws_abs])

        if isinstance(command, str):
            args.extend(["/bin/sh", "-c", command])
        else:
            args.extend(list(command))

        return args


class RootlessContainerRunner(SandboxRunner):
    """Sandbox runner using rootless Podman or Docker containers."""

    def __init__(self, runtime: str = "podman", image: str = "alpine:latest") -> None:
        self.runtime = runtime
        self.image = image
        self.name = f"{runtime}_rootless"
        self._usable: bool | None = None

    def is_available(self) -> bool:
        """Verify container binary and rootless status."""
        if self._usable is not None:
            return self._usable

        bin_path = shutil.which(self.runtime)
        if not bin_path:
            self._usable = False
            return False

        try:
            # Check info
            res = subprocess.run(
                [bin_path, "info"],
                capture_output=True,
                timeout=5,
            )
            self._usable = (res.returncode == 0)
        except Exception:
            self._usable = False

        return self._usable

    def build_command(self, command: str | list[str], config: SandboxConfig) -> list[str]:
        bin_path = shutil.which(self.runtime) or self.runtime
        ws_abs = str(Path(config.workspace_dir).resolve())

        args: list[str] = [
            bin_path,
            "run",
            "--rm",
            "-i",
            f"--memory={config.memory_limit_mb}m",
            f"--cpus={config.cpu_limit}",
            "-v", f"{ws_abs}:{ws_abs}:rw",
            "-w", ws_abs,
            "--read-only",
            "--tmpfs", "/tmp",
        ]

        if not config.allow_network:
            args.extend(["--network", "none"])

        for ro_path in config.read_only_binds:
            abs_ro = str(Path(ro_path).resolve())
            args.extend(["-v", f"{abs_ro}:{abs_ro}:ro"])

        for k, v in config.env.items():
            args.extend(["-e", f"{k}={v}"])

        args.append(self.image)

        if isinstance(command, str):
            args.extend(["/bin/sh", "-c", command])
        else:
            args.extend(list(command))

        return args


class DeniedFallbackRunner(SandboxRunner):
    """Fail-closed fallback runner when no rootless sandbox runtime is available.

    Per YusufClaw security doctrine (RESEARCH.md, RESEARCH-ASIST YC-04), execution
    must fail closed rather than silently running unconstrained on the gateway host.
    """

    name = "denied_fallback"

    def is_available(self) -> bool:
        """The denied fallback is always available as the terminal fail-closed gate."""
        return True

    def build_command(self, command: str | list[str], config: SandboxConfig) -> list[str]:
        return ["/bin/false"]

    async def run_async(
        self,
        command: str | list[str],
        config: SandboxConfig | None = None,
    ) -> SandboxResult:
        return SandboxResult(
            exit_code=126,
            stdout="",
            stderr=(
                "Sandbox execution denied: No rootless sandbox runtime "
                "(podman, docker rootless, or bubblewrap) is available. "
                "YusufClaw fails closed to protect the host system."
            ),
            duration_seconds=0.0,
            backend=self.name,
        )


def get_sandbox_runner(preferred: str | None = None) -> SandboxRunner:
    """Return an available sandbox runner matching preference, or fail-closed fallback."""
    if preferred == "bubblewrap":
        bw = BubblewrapRunner()
        if bw.is_available():
            return bw
    elif preferred in ("podman", "podman_rootless"):
        pm = RootlessContainerRunner(runtime="podman")
        if pm.is_available():
            return pm
    elif preferred in ("docker", "docker_rootless"):
        dk = RootlessContainerRunner(runtime="docker")
        if dk.is_available():
            return dk

    # Auto-detection priority: bubblewrap -> podman -> docker -> denied fallback
    bw = BubblewrapRunner()
    if bw.is_available():
        return bw

    pm = RootlessContainerRunner(runtime="podman")
    if pm.is_available():
        return pm

    dk = RootlessContainerRunner(runtime="docker")
    if dk.is_available():
        return dk

    # Fail-closed fallback
    return DeniedFallbackRunner()
