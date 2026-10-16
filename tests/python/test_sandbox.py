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

    assert "bwrap" in cmd[0]
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
