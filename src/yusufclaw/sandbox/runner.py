"""Rootless sandbox runner abstraction for YusufClaw shell and code tools.

Provides execution isolation using:
- Bubblewrap (bwrap) unprivileged user namespaces and filesystem isolation.
- Podman / Docker rootless containers.
- Denied fallback runner: fails closed (denies execution) when no isolation runtime is available.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from yusufclaw.sandbox.action_log import ReversibleActionLog
from yusufclaw.sandbox.models import SandboxConfig, SandboxResult


def runtime_environment() -> dict[str, str]:
    """Use the same local runtime context for discovery, launch, and cleanup."""
    env = {
        k: os.environ[k]
        for k in ("HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
        if k in os.environ
    }
    env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
    return env


SECRET_NAMES = {
    ".ssh",
    ".aws",
    ".azure",
    ".kube",
    ".gnupg",
    "gcloud",
    "agent-data",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "credentials",
    "credentials.json",
    "application_default_credentials.json",
    ".yusufclaw_actions.jsonl",
}


def validate_mounts(config: SandboxConfig) -> None:
    """Reject secret-bearing mounts rather than exposing secrets read-only.

    Mount inputs must be owned by a trusted host operator and remain stable during launch.
    Symlinks and hardlinks are rejected to prevent aliases bypassing the denylist.
    """
    log = Path(config.action_log_path).resolve()
    for raw in [config.workspace_dir, *config.read_only_binds]:
        path = Path(raw).absolute()
        if raw in config.read_only_binds and not path.exists():
            raise ValueError("Read-only mount does not exist")
        resolved = path.resolve()
        if any(
            resolved == Path(p) or resolved.is_relative_to(Path(p))
            for p in ("/proc", "/sys", "/dev", "/etc", "/usr", "/bin", "/lib", "/lib64", "/sbin")
        ):
            raise ValueError("Mount overlaps reserved system paths")
        if ":" in str(path) or "," in str(path):
            raise ValueError("Mount path contains runtime option delimiters")
        if path != resolved or resolved == Path("/"):
            raise ValueError("Unsafe mount path or symlink")
        if log == resolved or log.is_relative_to(resolved):
            raise ValueError("Action log must be outside sandbox mounts")

        def scan_error(error: OSError) -> None:
            raise error

        candidates = [path]
        if path.is_dir():
            for root, dirs, files in os.walk(path, followlinks=False, onerror=scan_error):
                candidates.extend(Path(root) / name for name in dirs + files)
        for item in candidates:
            if any(
                part in SECRET_NAMES or part.startswith(".env") or ".sqlite" in part
                for part in item.parts
            ):
                raise ValueError("Mount contains a denied secret or agent-data path")
            if item.exists() and not (stat.S_ISREG(item.stat().st_mode) or item.is_dir()):
                raise ValueError("Mount contains a special file")
            if item.is_symlink() or (item.is_file() and item.stat().st_nlink > 1):
                raise ValueError("Mount contains a symlink or hardlink")


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
        log = ReversibleActionLog(cfg.action_log_path)
        result = SandboxResult(
            exit_code=126, stdout="", stderr="", duration_seconds=0, backend=self.name
        )
        run_id = uuid.uuid4().hex
        proc: asyncio.subprocess.Process | None = None
        try:
            validate_mounts(cfg)
            log.log_action(
                "sandbox",
                "sandbox_intent",
                cfg.workspace_dir,
                details={
                    "run_id": run_id,
                    "backend": self.name,
                    "command_sha256": hashlib.sha256(json.dumps(command).encode()).hexdigest(),
                    "allow_network": cfg.allow_network,
                    "memory_limit_mb": cfg.memory_limit_mb,
                    "cpu_limit": cfg.cpu_limit,
                    "timeout_seconds": cfg.timeout_seconds,
                },
                reversible=False,
            )
            if not self.is_available() or isinstance(self, DeniedFallbackRunner):
                raise SandboxUnavailableError("No usable rootless runtime; execution fails closed")
            Path(cfg.workspace_dir).mkdir(parents=True, exist_ok=True)
            cmd_arr = self.build_command(command, cfg)
            if isinstance(self, RootlessContainerRunner):
                cmd_arr[2:2] = ["--name", "yc-" + run_id]
            # Runtime control environment is separate from the payload environment.
            env = runtime_environment()
            proc = await asyncio.create_subprocess_exec(
                *cmd_arr,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
            )

            async def capture(stream: asyncio.StreamReader | None) -> bytes:
                assert stream is not None
                output = bytearray()
                while chunk := await stream.read(8192):
                    output.extend(chunk[: max(0, cfg.max_output_bytes - len(output))])
                return bytes(output)

            async with asyncio.timeout(cfg.timeout_seconds):
                out, err, _ = await asyncio.gather(
                    capture(proc.stdout), capture(proc.stderr), proc.wait()
                )
            result.exit_code = proc.returncode if proc.returncode is not None else 1
            result.stdout = out.decode("utf-8", errors="replace")
            result.stderr = err.decode("utf-8", errors="replace")
        except TimeoutError:
            result.exit_code = 124
            result.timed_out = True
            result.stderr = f"Execution timed out after {cfg.timeout_seconds}s"
        except asyncio.CancelledError:
            result.exit_code = 130
            result.stderr = "Execution cancelled"
            raise
        except Exception as exc:
            result.stderr = f"Sandbox execution denied or failed: {exc}"
        finally:
            if proc is not None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
                if isinstance(self, RootlessContainerRunner):
                    try:
                        cleanup = await asyncio.create_subprocess_exec(
                            shutil.which(self.runtime) or self.runtime,
                            "rm",
                            "--force",
                            "yc-" + run_id,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                            env=env,
                        )
                        try:
                            await asyncio.wait_for(cleanup.wait(), 5)
                        except TimeoutError:
                            cleanup.kill()
                            await cleanup.wait()
                    except OSError:
                        result.exit_code = result.exit_code or 1
                        result.stderr += (
                            "\nContainer cleanup failed; operator reconciliation required"
                        )
            result.duration_seconds = time.monotonic() - start_time
            log.log_action(
                "sandbox",
                "sandbox_result",
                cfg.workspace_dir,
                details={
                    "run_id": run_id,
                    "backend": self.name,
                    "exit_code": result.exit_code,
                    "timed_out": result.timed_out,
                    "duration_seconds": result.duration_seconds,
                },
                reversible=False,
                status="executed" if result.success else "failed",
            )
        return result

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
        if not bwrap_bin or not shutil.which("systemd-run") or os.geteuid() == 0:
            self._usable = False
            return False

        # Require unified cgroups with both resource controllers.
        try:
            controllers = Path("/sys/fs/cgroup/cgroup.controllers").read_text().split()
            if not {"cpu", "memory"}.issubset(controllers):
                self._usable = False
                return False
            with tempfile.TemporaryDirectory(prefix="yc-probe-") as workspace:
                res = subprocess.run(
                    self.build_command(["/bin/true"], SandboxConfig(workspace_dir=workspace)),
                    capture_output=True,
                    timeout=3,
                    env=runtime_environment(),
                )
            self._usable = res.returncode == 0
        except Exception:
            self._usable = False

        return self._usable

    def build_command(self, command: str | list[str], config: SandboxConfig) -> list[str]:
        validate_mounts(config)
        bwrap_bin = shutil.which("bwrap") or "bwrap"
        ws_abs = str(Path(config.workspace_dir).resolve())

        args: list[str] = [
            "systemd-run",
            "--user",
            "--scope",
            "--quiet",
            "-p",
            f"MemoryMax={config.memory_limit_mb}M",
            "-p",
            f"CPUQuota={config.cpu_limit * 100:g}%",
            "-p",
            "TasksMax=128",
            "-p",
            f"RuntimeMaxSec={config.timeout_seconds}s",
            bwrap_bin,
            "--unshare-user",
            "--cap-drop",
            "ALL",
            "--new-session",
            "--clearenv",
            "--setenv",
            "PATH",
            "/usr/local/bin:/usr/bin:/bin",
            "--setenv",
            "HOME",
            "/tmp",
            "--die-with-parent",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
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

        for key, value in config.env.items():
            args.extend(["--setenv", key, value])
        args.extend(["--remount-ro", "/", "--chdir", ws_abs])

        if isinstance(command, str):
            args.extend(["/bin/sh", "-c", command])
        else:
            args.extend(list(command))

        return args


class RootlessContainerRunner(SandboxRunner):
    """Sandbox runner using rootless Podman or Docker containers."""

    def __init__(self, runtime: str = "podman", image: str = "alpine:latest") -> None:
        if runtime not in {"podman", "docker"}:
            raise ValueError("Unsupported container runtime")
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
                [bin_path, "info", "--format", "{{json .}}"],
                capture_output=True,
                timeout=5,
                env=runtime_environment(),
            )
            info = json.loads(res.stdout) if res.returncode == 0 else {}
            if self.runtime == "podman":
                self._usable = (
                    info.get("host", {}).get("security", {}).get("rootless") is True
                    and info.get("host", {}).get("cgroupVersion") == "v2"
                )
            else:
                self._usable = (
                    "name=rootless" in info.get("SecurityOptions", [])
                    and info.get("CgroupVersion") == "2"
                    and info.get("CgroupDriver") == "systemd"
                )
        except Exception:
            self._usable = False

        return self._usable

    def build_command(self, command: str | list[str], config: SandboxConfig) -> list[str]:
        validate_mounts(config)
        bin_path = shutil.which(self.runtime) or self.runtime
        ws_abs = str(Path(config.workspace_dir).resolve())

        args: list[str] = [
            bin_path,
            "run",
            "--rm",
            "--pull=never",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=128",
            "-i",
            f"--memory={config.memory_limit_mb}m",
            f"--cpus={config.cpu_limit}",
            "-v",
            f"{ws_abs}:{ws_abs}:rw",
            "-w",
            ws_abs,
            "--read-only",
            "--tmpfs",
            "/tmp",
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
