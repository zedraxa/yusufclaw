"""Tests for YusufClaw sandbox runner abstraction and reversible action log."""

from __future__ import annotations

from pathlib import Path

import pytest

from yusufclaw.sandbox.action_log import ReversibleActionLog
from yusufclaw.sandbox.models import SandboxConfig
from yusufclaw.sandbox.runner import (
    BubblewrapRunner,
    DeniedFallbackRunner,
    RootlessContainerRunner,
    get_sandbox_runner,
)


def test_sandbox_config_defaults() -> None:
    """Verify default sandbox security boundaries (no network, 512MB, 30s)."""
    cfg = SandboxConfig()
    assert not cfg.allow_network
    assert cfg.memory_limit_mb == 512
    assert cfg.cpu_limit == 1.0
    assert cfg.timeout_seconds == 30.0
    assert cfg.max_output_bytes == 65536
    assert cfg.workspace_dir == ".data/workspace"


def test_bubblewrap_command_construction() -> None:
    """Verify BubblewrapRunner command generation enforces isolation flags."""
    runner = BubblewrapRunner()
    cfg = SandboxConfig(
        workspace_dir="/tmp/fake_workspace",
        allow_network=False,
    )
    cmd = runner.build_command("echo 'hello'", cfg)

    assert any("bwrap" in part for part in cmd)
    assert "MemoryMax=512M" in cmd
    assert "CPUQuota=100%" in cmd
    assert "--unshare-user" in cmd
    assert "--clearenv" in cmd
    assert "--remount-ro" in cmd
    assert "--unshare-net" in cmd
    assert "--tmpfs" in cmd
    assert "/tmp" in cmd
    assert "--bind" in cmd
    assert "/tmp/fake_workspace" in cmd

    # When network is allowed, --unshare-net should not be present
    cfg_net = SandboxConfig(workspace_dir="/tmp/fake_workspace", allow_network=True)
    cmd_net = runner.build_command("echo 'hello'", cfg_net)
    assert "--unshare-net" not in cmd_net


def test_rootless_container_command_construction() -> None:
    """Verify RootlessContainerRunner command generation enforces container isolation."""
    runner = RootlessContainerRunner(runtime="podman", image="alpine:3.20")
    cfg = SandboxConfig(
        workspace_dir="/tmp/fake_workspace",
        memory_limit_mb=256,
        cpu_limit=0.5,
        allow_network=False,
    )
    cmd = runner.build_command(["ls", "-la"], cfg)

    assert "podman" in cmd[0]
    assert "run" in cmd
    assert "--network" in cmd
    assert "none" in cmd
    assert "--memory=256m" in cmd
    assert "--cpus=0.5" in cmd
    assert "--read-only" in cmd
    assert "alpine:3.20" in cmd


def test_denied_fallback_runner() -> None:
    """Verify DeniedFallbackRunner fails closed per YusufClaw security doctrine."""
    runner = DeniedFallbackRunner()
    assert runner.is_available()

    res = runner.run("ls -la")
    assert res.exit_code == 126
    assert not res.success
    assert res.backend == "denied_fallback"
    assert "fails closed" in res.stderr.lower()


def test_get_sandbox_runner_selection() -> None:
    """Verify get_sandbox_runner chooses available runner or denied fallback."""
    runner = get_sandbox_runner()
    # It must return an instance of SandboxRunner
    assert runner is not None
    assert runner.name in ("bubblewrap", "podman_rootless", "docker_rootless", "denied_fallback")


@pytest.mark.skipif(
    not BubblewrapRunner().is_available(),
    reason="Bubblewrap unprivileged sandbox is not available in this environment",
)
def test_bubblewrap_real_execution(tmp_path: Path) -> None:
    """Test real bwrap execution when runtime is available on host."""
    runner = BubblewrapRunner()
    cfg = SandboxConfig(workspace_dir=str(tmp_path), timeout_seconds=5.0)
    res = runner.run("echo 'sandboxed'", cfg)
    assert res.success
    assert "sandboxed" in res.stdout


def test_reversible_action_log_lifecycle(tmp_path: Path) -> None:
    """Test append-only action log and reversible undo operations."""
    log_file = tmp_path / "actions.jsonl"
    action_log = ReversibleActionLog(log_path=log_file)

    # 1. Test file creation undo (delete_file)
    test_file = tmp_path / "created.txt"
    test_file.write_text("temporary content", encoding="utf-8")
    assert test_file.exists()

    entry_create = action_log.log_action(
        tool_name="file_write",
        action_type="file_create",
        target=str(test_file),
        undo_hint={"type": "delete_file"},
        reversible=True,
    )

    undo_res = action_log.undo(entry_create.action_id)
    assert undo_res.success
    assert not test_file.exists()  # Undone: file removed!

    # Second undo should fail (already undone)
    undo_again = action_log.undo(entry_create.action_id)
    assert not undo_again.success
    assert "already undone" in undo_again.message

    # 2. Test file overwrite undo (restore_file)
    target_file = tmp_path / "config.txt"
    target_file.write_text("version=1.0", encoding="utf-8")

    # Overwrite file
    target_file.write_text("version=2.0-malformed", encoding="utf-8")
    entry_mod = action_log.log_action(
        tool_name="file_write",
        action_type="file_overwrite",
        target=str(target_file),
        undo_hint={"type": "restore_file", "previous_content": "version=1.0"},
        reversible=True,
    )

    undo_mod = action_log.undo(entry_mod.action_id)
    assert undo_mod.success
    assert target_file.read_text(encoding="utf-8") == "version=1.0"  # Restored!

    # 3. Test directory creation undo (remove_dir)
    test_dir = tmp_path / "subproject"
    test_dir.mkdir()
    (test_dir / "foo.txt").write_text("bar", encoding="utf-8")

    entry_dir = action_log.log_action(
        tool_name="shell",
        action_type="dir_create",
        target=str(test_dir),
        undo_hint={"type": "remove_dir"},
        reversible=True,
    )

    undo_dir = action_log.undo(entry_dir.action_id)
    assert undo_dir.success
    assert not test_dir.exists()

    # 4. Non-reversible action
    entry_non_rev = action_log.log_action(
        tool_name="shell",
        action_type="exec",
        target="external_api",
        reversible=False,
    )
    undo_non_rev = action_log.undo(entry_non_rev.action_id)
    assert not undo_non_rev.success
    assert "non-reversible" in undo_non_rev.message

    # Verify log entries preserved in append-only JSONL
    entries = action_log.get_entries()
    assert len(entries) >= 4


@pytest.mark.parametrize(
    "name",
    [
        ".ssh/key",
        ".env",
        ".env.production",
        ".aws/config",
        ".azure/token",
        ".config/gcloud/token",
        "agent-data/log",
        "id_ed25519",
        "db.sqlite3",
        "credentials.json",
    ],
)
def test_secret_mount_denial(tmp_path: Path, name: str) -> None:
    secret = tmp_path / name
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("secret")
    for runner in (
        BubblewrapRunner(),
        RootlessContainerRunner("podman"),
        RootlessContainerRunner("docker"),
    ):
        with pytest.raises(ValueError, match="denied secret"):
            runner.build_command("cat *", SandboxConfig(workspace_dir=str(tmp_path)))
        with pytest.raises(ValueError):
            runner.build_command("true", SandboxConfig(read_only_binds=[str(tmp_path)]))


@pytest.mark.parametrize(
    "field", ["memory_limit_mb", "cpu_limit", "timeout_seconds", "max_output_bytes"]
)
def test_invalid_limits(field: str) -> None:
    with pytest.raises(ValueError):
        SandboxConfig.model_validate({field: 0})


def test_link_denial(tmp_path: Path) -> None:
    (tmp_path / "alias").symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="symlink"):
        BubblewrapRunner().build_command("true", SandboxConfig(workspace_dir=str(tmp_path)))


def test_runtime_rootless_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    from subprocess import CompletedProcess

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    for runtime, info, expected in [
        ("docker", {}, False),
        ("podman", {"host": {"security": {"rootless": False}}}, False),
        ("podman", {"host": {"security": {"rootless": True}, "cgroupVersion": "v2"}}, True),
        (
            "docker",
            {"SecurityOptions": ["name=rootless"], "CgroupVersion": "2", "CgroupDriver": "systemd"},
            True,
        ),
    ]:
        monkeypatch.setattr(
            "subprocess.run",
            lambda *args, info=info, **kwargs: CompletedProcess([], 0, json.dumps(info)),
        )
        assert RootlessContainerRunner(runtime).is_available() is expected


def test_no_runtime_and_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    runner = get_sandbox_runner()
    cfg = SandboxConfig(
        workspace_dir=str(tmp_path / "work"), action_log_path=str(tmp_path / "audit")
    )
    result = runner.run("touch escaped", cfg)
    assert result.exit_code == 126
    assert not (tmp_path / "work" / "escaped").exists()
    entries = ReversibleActionLog(cfg.action_log_path).get_entries()
    assert [e.action_type for e in entries] == ["sandbox_intent", "sandbox_result"]
    assert entries[-1].status == "failed"
    assert all(not e.reversible for e in entries)
    assert entries[0].details["run_id"] == entries[1].details["run_id"]


async def test_shell_uses_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from yusufclaw.sandbox.models import SandboxResult
    from yusufclaw.tools.shell import create_shell_tool

    class FakeRunner:
        async def run_async(self, command: str, config: SandboxConfig) -> SandboxResult:
            assert "python3 -c" in command
            assert config.workspace_dir == str(tmp_path)
            assert not config.allow_network
            return SandboxResult(
                exit_code=0, stdout="isolated", stderr="", duration_seconds=0, backend="fake"
            )

    monkeypatch.setattr("yusufclaw.tools.shell.get_sandbox_runner", lambda: FakeRunner())
    tool = create_shell_tool(str(tmp_path))
    assert await tool.execute_fn({"command": "python3 -c 'print(1)'"}) == "isolated"


@pytest.mark.parametrize("command,code", [("printf success", 0), ("exit 7", 7), ("sleep 10", 124)])
def test_execution_audit_and_timeout(tmp_path: Path, command: str, code: int) -> None:
    from yusufclaw.sandbox.runner import SandboxRunner

    class TestRunner(SandboxRunner):
        name = "test-only"

        def is_available(self) -> bool:
            return True

        def build_command(self, command: str | list[str], config: SandboxConfig) -> list[str]:
            assert isinstance(command, str)
            return ["/bin/sh", "-c", command]

    cfg = SandboxConfig(
        workspace_dir=str(tmp_path / "work"),
        action_log_path=str(tmp_path / "audit"),
        timeout_seconds=0.1,
    )
    result = TestRunner().run(command, cfg)
    assert result.exit_code == code
    assert result.timed_out == (code == 124)
    entries = ReversibleActionLog(cfg.action_log_path).get_entries()
    assert len(entries) == 2
    assert entries[-1].details["exit_code"] == code
    assert entries[-1].status == ("executed" if code == 0 else "failed")


def test_log_not_exposed(tmp_path: Path) -> None:
    cfg = SandboxConfig(workspace_dir=str(tmp_path), action_log_path=str(tmp_path / "log"))
    with pytest.raises(ValueError, match="outside"):
        RootlessContainerRunner().build_command("true", cfg)


async def test_cancellation_is_audited(tmp_path: Path) -> None:
    import asyncio

    from yusufclaw.sandbox.runner import SandboxRunner

    started = asyncio.Event()

    class TestRunner(SandboxRunner):
        name = "test-only"

        def is_available(self) -> bool:
            return True

        def build_command(self, command: str | list[str], config: SandboxConfig) -> list[str]:
            started.set()
            return ["/bin/sh", "-c", "sleep 10"]

    cfg = SandboxConfig(
        workspace_dir=str(tmp_path / "work"), action_log_path=str(tmp_path / "audit")
    )
    task = asyncio.create_task(TestRunner().run_async("test", cfg))
    await started.wait()
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    entries = ReversibleActionLog(cfg.action_log_path).get_entries()
    assert entries[-1].details["exit_code"] == 130
    assert entries[-1].status == "failed"
