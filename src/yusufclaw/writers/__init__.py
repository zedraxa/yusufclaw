"""YusufClaw Writer Delegates Package (P2-01).

Implements bounded delegation to identified external coding CLIs (agy, codex):
- Protocol probe (version and availability checks)
- Isolated git worktree workspace lifecycle
- Child capability subset and strict environment scrubbing (no host secrets)
- Concurrency limiting (at most 2 concurrent children, depth 1)
- Parent verification hook (runs pytest or custom test command in child worktree)
- Optional durable job queue integration (YUSUFCLAW_QUEUE_ENABLED)
- Structured results with changed file paths and execution summaries
"""

from yusufclaw.writers.models import (
    ChildBudget,
    ProtocolProbeResult,
    VerificationHookResult,
    WriterResult,
    WriterSpec,
)
from yusufclaw.writers.probe import CLIProtocolProbe, probe_cli
from yusufclaw.writers.runner import WriterDelegateRunner, execute_writer
from yusufclaw.writers.security import (
    SAFE_ENV_ALLOWLIST,
    SECRET_PATTERNS,
    SecurityViolationError,
    is_secret_variable,
    scrub_environment,
    validate_workspace_path,
)
from yusufclaw.writers.verifier import WriterVerifier
from yusufclaw.writers.workspace import (
    WorkspaceConflictError,
    WorkspaceManager,
    WorktreeWorkspace,
)

__all__ = [
    "CLIProtocolProbe",
    "ChildBudget",
    "ProtocolProbeResult",
    "SAFE_ENV_ALLOWLIST",
    "SECRET_PATTERNS",
    "SecurityViolationError",
    "VerificationHookResult",
    "WorkspaceConflictError",
    "WorkspaceManager",
    "WorktreeWorkspace",
    "WriterDelegateRunner",
    "WriterResult",
    "WriterSpec",
    "WriterVerifier",
    "execute_writer",
    "is_secret_variable",
    "probe_cli",
    "scrub_environment",
    "validate_workspace_path",
]
