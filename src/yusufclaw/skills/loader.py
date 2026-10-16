"""Trusted local markdown skill loader for YusufClaw.

Loads only trusted local markdown instruction files:
- No scripts, includes, symlink follow, or frontmatter execution.
- Strict nesting depth limit (<= 8).
- Individual file size limit (<= 32,768 bytes) and cumulative limit (<= 131,072 bytes).
- Preserves alphabetical ordering of entries.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

MAX_DEPTH = 8
MAX_FILE_BYTES = 32_768
MAX_TOTAL_BYTES = 131_072


def _load_skills_impl(directory: str | Path = "skills") -> str:
    path_obj = Path(directory)
    try:
        st = os.lstat(path_obj)
    except FileNotFoundError:
        return ""

    if not stat.S_ISDIR(st.st_mode):
        raise ValueError("Skills path must be a directory")

    parts: list[str] = []
    total_bytes = 0

    def visit(current_path: Path, prefix: str, depth: int) -> None:
        nonlocal total_bytes
        if depth > MAX_DEPTH:
            raise ValueError("Skills directory nesting limit exceeded")

        try:
            raw_entries = list(os.scandir(current_path))
        except FileNotFoundError:
            return

        entries = sorted(raw_entries, key=lambda e: e.name)

        for entry in entries:
            # Skip hidden files and symlinks
            if entry.name.startswith(".") or entry.is_symlink():
                continue

            name = f"{prefix}{entry.name}"
            entry_path = Path(entry.path)

            if entry.is_dir(follow_symlinks=False):
                visit(entry_path, f"{name}/", depth + 1)
            elif (
                entry.is_file(follow_symlinks=False)
                and entry.name.lower().endswith(".md")
                and entry.name.lower() != "readme.md"
            ):
                file_stat = os.lstat(entry_path)
                if file_stat.st_size > MAX_FILE_BYTES:
                    raise ValueError("Skill instructions exceed size limit")
                total_bytes += file_stat.st_size
                if total_bytes > MAX_TOTAL_BYTES:
                    raise ValueError("Skill instructions exceed size limit")

                content = entry_path.read_text(encoding="utf-8")
                stripped = content.strip()
                if stripped:
                    parts.append(f"### Skill: {name}\n{stripped}")

    visit(path_obj, "", 0)
    if not parts:
        return ""
    return f"Trusted local skill instructions:\n\n{'\n\n'.join(parts)}"


def load_skills_sync(directory: str | Path = "skills") -> str:
    """Synchronously load trusted local markdown skills from the given directory."""
    return _load_skills_impl(directory)


async def load_skills(directory: str | Path = "skills") -> str:
    """Asynchronously load trusted local markdown skills from the given directory."""
    return _load_skills_impl(directory)


# CamelCase alias matching TypeScript core export
loadSkills = load_skills

__all__ = [
    "MAX_DEPTH",
    "MAX_FILE_BYTES",
    "MAX_TOTAL_BYTES",
    "loadSkills",
    "load_skills",
    "load_skills_sync",
]
