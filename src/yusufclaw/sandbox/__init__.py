"""Sandbox subsystem for YusufClaw.

Implements rootless sandbox runner abstraction (Bubblewrap, Podman, Docker rootless),
fail-closed denied fallback runner, resource/time limits, no network by default,
and append-only reversible action logs.
"""

from yusufclaw.sandbox.action_log import ReversibleActionLog
from yusufclaw.sandbox.models import (
    ActionLogEntry,
    ActionStatus,
    SandboxConfig,
    SandboxResult,
    UndoResult,
)
from yusufclaw.sandbox.runner import (
    BubblewrapRunner,
    DeniedFallbackRunner,
    RootlessContainerRunner,
    SandboxRunner,
    SandboxUnavailableError,
    get_sandbox_runner,
)

__all__ = [
    "ActionLogEntry",
    "ActionStatus",
    "BubblewrapRunner",
    "DeniedFallbackRunner",
    "ReversibleActionLog",
    "RootlessContainerRunner",
    "SandboxConfig",
    "SandboxResult",
    "SandboxRunner",
    "SandboxUnavailableError",
    "UndoResult",
    "get_sandbox_runner",
]
