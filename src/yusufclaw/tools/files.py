"""Filesystem tools with workspace sandboxing and restricted secret path denial.

Implements YC-01 defense-in-depth:
- Confinement to workspace root (.data/workspace by default).
- Absolute denial for secret files (.env*, *.sqlite*, ~/.aws*, .git/config, id_rsa).
- Path traversal and symlink escape defenses.
"""

from __future__ import annotations

import os
import re
from typing import Any

from yusufclaw.security.models import ToolManifest, ToolResources
from yusufclaw.tools.manifest import GuardedTool

DEFAULT_WORKSPACE_DIR = ".data/workspace"

_SQLITE_PATTERN = re.compile(r"\.sqlite($|-|\b|\d)", re.IGNORECASE)


def is_restricted_secret_path(path_str: str) -> bool:
    """Check if a path references restricted sensitive secrets."""
    if not path_str or not isinstance(path_str, str):
        return False

    normalized = path_str.replace("\\", "/").strip()
    segments = [s for s in normalized.split("/") if s]
    base_name = segments[-1] if segments else ""

    # 1. .env* (.env, .env.local, .env.production, etc.)
    if base_name == ".env" or base_name.startswith(".env.") or base_name.startswith(".env"):
        return True
    if any(s == ".env" or s.startswith(".env.") or s.startswith(".env") for s in segments):
        return True

    # 2. *.sqlite (*.sqlite, *.sqlite-wal, *.sqlite-journal, *.sqlite3)
    if _SQLITE_PATTERN.search(base_name):
        return True
    if any(_SQLITE_PATTERN.search(s) for s in segments):
        return True

    # 3. ~/.aws or .aws
    if ".aws" in segments or ".aws/" in normalized or normalized.endswith(".aws"):
        return True

    # 4. .git/config
    if ".git/config" in normalized or (base_name == "config" and ".git" in segments):
        return True

    # 5. SSH keys
    if base_name in ("id_rsa", "id_ed25519", "id_ecdsa"):
        return True

    return False


def resolve_safe_path(workspace_root: str, user_path: str) -> str:
    """Confine a path strictly to workspace_root, preventing path traversal or secret leaks."""
    if not isinstance(user_path, str) or not user_path.strip():
        raise ValueError("Path must be a non-empty string")

    if "\0" in user_path:
        raise ValueError("Invalid path: null bytes are forbidden")

    if is_restricted_secret_path(user_path):
        raise PermissionError(f'Access to restricted sensitive path is denied: "{user_path}"')

    root_abs = os.path.abspath(workspace_root)
    trimmed = user_path.strip()
    normalized = os.path.normpath(trimmed)

    resolved = normalized if os.path.isabs(normalized) else os.path.abspath(os.path.join(root_abs, normalized))

    if is_restricted_secret_path(resolved) or is_restricted_secret_path(os.path.relpath(resolved, root_abs)):
        raise PermissionError(f'Access to restricted sensitive path is denied: "{user_path}"')

    rel = os.path.relpath(resolved, root_abs)
    if rel.startswith("..") or os.path.isabs(rel):
        raise PermissionError(f'Path escape detected: "{user_path}" is outside workspace root')

    # If file exists, verify realpath to block symlinks resolving outside
    if os.path.exists(resolved):
        real = os.path.realpath(resolved)
        if is_restricted_secret_path(real):
            raise PermissionError(f'Access to restricted sensitive path is denied: "{user_path}"')
        real_rel = os.path.relpath(real, root_abs)
        if real_rel.startswith("..") or os.path.isabs(real_rel):
            raise PermissionError(f'Path escape detected: symlink "{user_path}" resolves outside workspace root')
        return real

    # If file doesn't exist yet, check nearest existing ancestor directory
    ancestor = os.path.dirname(resolved)
    while len(ancestor) >= len(root_abs):
        if os.path.exists(ancestor):
            real_ancestor = os.path.realpath(ancestor)
            if is_restricted_secret_path(real_ancestor):
                raise PermissionError(f'Access to restricted sensitive path is denied: "{user_path}"')
            real_rel = os.path.relpath(real_ancestor, root_abs)
            if real_rel.startswith("..") or os.path.isabs(real_rel):
                raise PermissionError("Path escape detected: parent directory symlink resolves outside workspace root")
            break
        ancestor = os.path.dirname(ancestor)

    return resolved


def create_file_read_tool(
    workspace_root: str = DEFAULT_WORKSPACE_DIR,
    max_read_bytes: int = 64_000,
) -> GuardedTool:
    """Create guarded file_read tool."""
    root = os.path.abspath(workspace_root)
    os.makedirs(root, exist_ok=True)

    manifest = ToolManifest(
        id="file_read",
        version="1.0.0",
        source="builtin",
        effects=["read_private"],
        resources=ToolResources(paths=[root]),
    )

    async def execute(input_args: dict[str, Any]) -> str:
        raw_path = str(input_args.get("path", ""))
        safe_path = resolve_safe_path(root, raw_path)

        if not os.path.exists(safe_path):
            raise FileNotFoundError(f"File not found: {raw_path}")

        if os.path.isdir(safe_path):
            raise IsADirectoryError(f"Path is a directory, not a file: {raw_path}")

        req_max = int(input_args.get("maxBytes", max_read_bytes))
        effective_max = min(max(1, req_max), 1_000_000)
        encoding = str(input_args.get("encoding", "utf-8"))

        with open(safe_path, "rb") as f:
            data = f.read(effective_max + 1)

        truncated = len(data) > effective_max
        if truncated:
            data = data[:effective_max]

        content = data.decode(encoding, errors="replace")
        if truncated:
            content += f"\n[File truncated: exceeded {effective_max} bytes limit]"
        return content

    return GuardedTool(
        name="file_read",
        description="Read the contents of a file within the workspace root.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file relative to the workspace root."},
                "encoding": {"type": "string", "default": "utf-8"},
                "maxBytes": {"type": "number", "default": 64000},
            },
            "required": ["path"],
        },
        manifest=manifest,
        execute_fn=execute,
        requires_approval=False,
    )


def create_file_write_tool(
    workspace_root: str = DEFAULT_WORKSPACE_DIR,
) -> GuardedTool:
    """Create guarded file_write tool requiring approval."""
    root = os.path.abspath(workspace_root)
    os.makedirs(root, exist_ok=True)

    manifest = ToolManifest(
        id="file_write",
        version="1.0.0",
        source="builtin",
        effects=["write_local"],
        resources=ToolResources(paths=[root]),
    )

    async def execute(input_args: dict[str, Any]) -> str:
        raw_path = str(input_args.get("path", ""))
        content = str(input_args.get("content", ""))
        encoding = str(input_args.get("encoding", "utf-8"))

        safe_path = resolve_safe_path(root, raw_path)
        os.makedirs(os.path.dirname(safe_path), exist_ok=True)

        encoded_data = content.encode(encoding)
        with open(safe_path, "wb") as f:
            f.write(encoded_data)

        return f"File successfully written: {raw_path} ({len(encoded_data)} bytes)"

    return GuardedTool(
        name="file_write",
        description="Write or overwrite a file within the workspace root. Requires explicit user approval.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file relative to workspace root."},
                "content": {"type": "string", "description": "Content to write to the file."},
                "encoding": {"type": "string", "default": "utf-8"},
            },
            "required": ["path", "content"],
        },
        manifest=manifest,
        execute_fn=execute,
        requires_approval=True,
    )


def create_file_list_tool(
    workspace_root: str = DEFAULT_WORKSPACE_DIR,
    max_list_entries: int = 200,
) -> GuardedTool:
    """Create guarded file_list tool."""
    root = os.path.abspath(workspace_root)
    os.makedirs(root, exist_ok=True)

    manifest = ToolManifest(
        id="file_list",
        version="1.0.0",
        source="builtin",
        effects=["read_private"],
        resources=ToolResources(paths=[root]),
    )

    async def execute(input_args: dict[str, Any]) -> str:
        raw_path = str(input_args.get("path", "")).strip() or "."
        safe_path = resolve_safe_path(root, raw_path)

        if not os.path.exists(safe_path):
            raise FileNotFoundError(f"Directory not found: {raw_path}")

        if not os.path.isdir(safe_path):
            raise NotADirectoryError(f"Path is a file, not a directory: {raw_path}")

        recursive = bool(input_args.get("recursive", False))
        req_max = int(input_args.get("maxEntries", max_list_entries))
        effective_max = min(max(1, req_max), 1000)

        entries: list[str] = []

        def scan(current_dir: str, rel_prefix: str) -> None:
            if len(entries) >= effective_max:
                return
            try:
                dir_entries = sorted(os.scandir(current_dir), key=lambda e: e.name)
            except Exception:
                return

            for entry in dir_entries:
                if len(entries) >= effective_max:
                    break
                if entry.name in (".git", "node_modules", "__pycache__", ".venv"):
                    continue

                entry_rel = f"{rel_prefix}/{entry.name}" if rel_prefix else entry.name
                if entry.is_dir(follow_symlinks=False):
                    entries.append(f"[DIR]  {entry_rel}")
                    if recursive:
                        scan(entry.path, entry_rel)
                else:
                    try:
                        sz = entry.stat().st_size
                        entries.append(f"[FILE] {entry_rel} ({sz} bytes)")
                    except Exception:
                        entries.append(f"[FILE] {entry_rel}")

        scan(safe_path, "" if raw_path == "." else raw_path)

        if not entries:
            return f"[Directory is empty: {raw_path}]"

        result = "\n".join(entries)
        if len(entries) >= effective_max:
            result += f"\n[List truncated: reached maximum {effective_max} entries]"
        return result

    return GuardedTool(
        name="file_list",
        description="List files and directories within a directory inside the workspace root.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "."},
                "recursive": {"type": "boolean", "default": False},
                "maxEntries": {"type": "number", "default": 200},
            },
        },
        manifest=manifest,
        execute_fn=execute,
        requires_approval=False,
    )
