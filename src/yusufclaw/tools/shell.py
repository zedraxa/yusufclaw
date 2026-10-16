"""Shell tool with allowlists, path traversal checks, and dangerous command approvals.

Implements YC-01 defense-in-depth:
- Path traversal rejection (cat ../.env).
- Detection of restricted paths (.env*, *.sqlite, ~/.aws, .git/config).
- Command allowlists and destructive command approval gating.
- Clean process environment preventing token leakage.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Any

from yusufclaw.sandbox.models import SandboxConfig
from yusufclaw.sandbox.runner import get_sandbox_runner
from yusufclaw.security.models import ToolManifest, ToolResources
from yusufclaw.tools.files import DEFAULT_WORKSPACE_DIR
from yusufclaw.tools.manifest import GuardedTool

DEFAULT_ALLOWED_COMMANDS = [
    "ls",
    "dir",
    "cat",
    "head",
    "tail",
    "grep",
    "egrep",
    "fgrep",
    "find",
    "echo",
    "printf",
    "pwd",
    "which",
    "whereis",
    "wc",
    "diff",
    "sort",
    "uniq",
    "date",
    "whoami",
    "uname",
    "env",
    "git",
    "node",
    "pnpm",
    "npm",
    "npx",
    "yarn",
    "python",
    "python3",
    "mkdir",
    "cp",
    "mv",
    "rm",
    "touch",
    "rmdir",
    "sed",
    "awk",
    "tr",
    "cut",
    "tar",
    "gzip",
    "gunzip",
    "zip",
    "unzip",
    "curl",
    "wget",
    "jq",
    "sleep",
]

SAFE_READ_ONLY_BINARIES = {
    "pwd",
    "whoami",
    "uname",
    "date",
    "which",
    "whereis",
    "wc",
    "ls",
    "dir",
    "cat",
    "head",
    "tail",
    "grep",
    "egrep",
    "fgrep",
    "diff",
    "sort",
    "uniq",
    "jq",
}

_RESTRICTED_ENV_RE = re.compile(r"(^|[\s/\"'=\\])\.env([a-zA-Z0-9_.-]*)")
_RESTRICTED_SQLITE_RE = re.compile(r"\.sqlite($|-|\b|\d)", re.IGNORECASE)
_RESTRICTED_AWS_RE = re.compile(r"(^|[\s/\"'=\\])(\~?\/)?\.aws([/\s\"'\\$]|$)")
_RESTRICTED_GIT_RE = re.compile(r"\.git/config")
_TRAVERSAL_RE = re.compile(r"(^|[\s\"'=])\.\./|/\.\.(/|$|\s|[\"'])")

_DESTRUCTIVE_RE = re.compile(
    r"\b(rm|rmdir|mv|cp|touch|mkdir|kill|pkill|killall|chmod|chown|chgrp|dd|truncate|tee|unlink|reboot|shutdown|poweroff|systemctl|service)\b"
)
_GIT_READ_ONLY_RE = re.compile(
    r"(\bgit\s+(status|log|diff|show|rev-parse|describe)(\s|$))|(\bgit\s+branch(\s|$)[^-(d|D|m|M)]*)"
)
_VERSION_ONLY_RE = re.compile(r"^(node|pnpm|npm|python|python3)\s+(-v|--version|-V)$")
_INTERPRETER_RE = re.compile(r"\b(node|pnpm|npm|npx|yarn|python|python3)\b")


def contains_restricted_path(command: str) -> bool:
    """Check whether a shell command references restricted files or path traversals."""
    if not command or not isinstance(command, str):
        return False

    if _RESTRICTED_ENV_RE.search(command):
        return True
    if _RESTRICTED_SQLITE_RE.search(command):
        return True
    if _RESTRICTED_AWS_RE.search(command):
        return True
    if _RESTRICTED_GIT_RE.search(command):
        return True
    if _TRAVERSAL_RE.search(command):
        return True

    return False


def extract_command_binaries(command: str) -> list[str]:
    """Extract binary executable names from a command pipeline or chain."""
    binaries: list[str] = []
    segments = re.split(r"[|;&\n]+", command)

    for raw_segment in segments:
        trimmed = raw_segment.strip()
        if not trimmed:
            continue

        tokens = trimmed.split()
        for token in tokens:
            if "=" in token and not token.startswith("-"):
                continue  # skip environment assignments e.g. VAR=val

            clean = token.lstrip("./\\").split("/")[-1]
            if clean:
                binaries.append(clean)
                break

    return binaries


def is_dangerous_command(command: str) -> bool:
    """Determine whether a command performs writes/mutations and requires approval."""
    trimmed = command.strip()
    if not trimmed:
        return True

    # Redirection writes/overwrites files
    if re.search(r"[0-9&]?>>?", trimmed):
        return True

    # Command substitutions
    if re.search(r"\$\(|\`", trimmed):
        return True

    # Destructive binaries
    if _DESTRUCTIVE_RE.search(trimmed):
        return True

    # Git mutations
    if re.search(r"\bgit\b", trimmed):
        is_read_only = bool(
            re.search(r"\bgit\s+(status|log|diff|show|rev-parse|describe)(\s|$)", trimmed)
            or (
                re.search(r"\bgit\s+branch(\s|$)", trimmed)
                and not re.search(r"-(d|D|m|M)", trimmed)
            )
        )
        if not is_read_only:
            return True

    # Language runtimes
    if _INTERPRETER_RE.search(trimmed):
        if not _VERSION_ONLY_RE.match(trimmed):
            return True

    # Restricted path checks
    if contains_restricted_path(trimmed):
        return True

    binaries = extract_command_binaries(trimmed)
    if not binaries:
        return True

    for b in binaries:
        if b in ("echo", "printf"):
            continue
        if b == "git":
            continue
        if _VERSION_ONLY_RE.match(trimmed) and b in ("node", "pnpm", "npm", "python", "python3"):
            continue
        if b not in SAFE_READ_ONLY_BINARIES:
            return True

    return False


def create_shell_tool(
    workspace_dir: str = DEFAULT_WORKSPACE_DIR,
    allowed_commands: list[str] | None = None,
    default_timeout_ms: int = 30_000,
    max_timeout_ms: int = 60_000,
    max_output_chars: int = 32_000,
) -> GuardedTool:
    """Create guarded shell tool with allowlists and path traversal defenses."""
    root = os.path.abspath(workspace_dir)
    os.makedirs(root, exist_ok=True)

    allowed = set(allowed_commands or DEFAULT_ALLOWED_COMMANDS)

    manifest = ToolManifest(
        id="shell",
        version="1.0.0",
        source="builtin",
        effects=["exec", "read_private", "write_local"],
        resources=ToolResources(paths=[root]),
    )

    async def execute(input_args: dict[str, Any]) -> str:
        raw_cmd = str(input_args.get("command", "")).strip()
        if not raw_cmd:
            raise ValueError("Command must be a non-empty string")

        if contains_restricted_path(raw_cmd):
            raise PermissionError(
                f"Command references restricted sensitive file or path traversal: {raw_cmd}"
            )

        binaries = extract_command_binaries(raw_cmd)
        for b in binaries:
            if b not in allowed:
                raise PermissionError(f"Command not allowed by allowlist: {b}")

        req_cwd = str(input_args.get("cwd", ".")).strip() or "."
        target_cwd = os.path.abspath(os.path.join(root, req_cwd))
        rel = os.path.relpath(target_cwd, root)
        if rel.startswith("..") or os.path.isabs(rel):
            raise PermissionError(f"Working directory outside workspace directory: {req_cwd}")

        req_timeout = int(input_args.get("timeoutMs", default_timeout_ms))
        effective_timeout = min(max(100, req_timeout), max_timeout_ms) / 1000.0

        if not Path(target_cwd).resolve().is_relative_to(Path(root).resolve()):
            raise PermissionError("Working directory escapes workspace through symlink")
        result = await get_sandbox_runner().run_async(
            f"cd {shlex.quote(target_cwd)} && {raw_cmd}",
            SandboxConfig(
                workspace_dir=root,
                timeout_seconds=effective_timeout,
                max_output_bytes=max_output_chars,
            ),
        )
        if result.timed_out:
            raise TimeoutError(f"Command timed out after {int(effective_timeout * 1000)}ms")
        output = result.stdout
        if result.stderr:
            output += ("\n[stderr]\n" if output else "") + result.stderr
        if result.exit_code:
            output += f"\n[exit code: {result.exit_code}]"
        return output[:max_output_chars] or "[Command completed with no output]"

    return GuardedTool(
        name="shell",
        description="Execute a shell command within the workspace directory. Dangerous or destructive commands require explicit user approval.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute."},
                "cwd": {
                    "type": "string",
                    "description": "Optional directory relative to workspace root.",
                },
                "timeoutMs": {"type": "number", "default": 30000},
            },
            "required": ["command"],
        },
        manifest=manifest,
        execute_fn=execute,
        requires_approval=lambda args: is_dangerous_command(str(args.get("command", ""))),
    )
