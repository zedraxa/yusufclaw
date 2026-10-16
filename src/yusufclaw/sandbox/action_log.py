"""Append-only reversible action log with undo hints for YusufClaw.

Records tool execution side-effects (file write, directory creation, mutations)
to a persistent JSONL log and provides deterministic undo capabilities.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yusufclaw.sandbox.models import ActionLogEntry, UndoResult


class ReversibleActionLog:
    """Append-only JSONL log tracking mutations and their reversible undo operations."""

    def __init__(self, log_path: str | Path = ".data/workspace/.yusufclaw_actions.jsonl") -> None:
        self.log_path = Path(log_path)

    def _ensure_log_file(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.log_path.exists():
            self.log_path.touch()

    def log_action(
        self,
        tool_name: str,
        action_type: str,
        target: str,
        details: dict[str, Any] | None = None,
        undo_hint: dict[str, Any] | None = None,
        reversible: bool = True,
    ) -> ActionLogEntry:
        """Append a new action entry to the action log."""
        self._ensure_log_file()
        action_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()

        entry = ActionLogEntry(
            action_id=action_id,
            timestamp=now,
            tool_name=tool_name,
            action_type=action_type,
            target=target,
            details=details or {},
            undo_hint=undo_hint,
            reversible=reversible,
            status="executed",
        )

        line = json.dumps(entry.model_dump(), separators=(",", ":"), ensure_ascii=False)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        return entry

    def get_entries(
        self,
        limit: int = 100,
        target: str | None = None,
        status: str | None = None,
    ) -> list[ActionLogEntry]:
        """Read entries from the append-only log."""
        if not self.log_path.exists():
            return []

        entries: list[ActionLogEntry] = []
        with open(self.log_path, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    data = json.loads(stripped)
                    entry = ActionLogEntry.model_validate(data)
                    if target and entry.target != target:
                        continue
                    if status and entry.status != status:
                        continue
                    entries.append(entry)
                except Exception:
                    continue

        return entries[-limit:]

    def is_undone(self, action_id: str) -> bool:
        """Check whether an undo_action compensation event exists for this action_id."""
        for entry in self.get_entries(limit=10000):
            if entry.action_type == "undo_action" and entry.target == action_id:
                return True
        return False

    def get_entry(self, action_id: str) -> ActionLogEntry | None:
        """Find an action entry by its UUID with derived status."""
        target_entry: ActionLogEntry | None = None
        for entry in reversed(self.get_entries(limit=10000)):
            if entry.action_id == action_id:
                target_entry = entry
                break

        if target_entry is not None and self.is_undone(action_id):
            target_entry = target_entry.model_copy(update={"status": "undone"})

        return target_entry

    def undo(self, action_id: str) -> UndoResult:
        """Attempt to undo an action using its recorded undo hint."""
        entry = self.get_entry(action_id)
        if entry is None:
            return UndoResult(
                action_id=action_id,
                success=False,
                message=f"Action '{action_id}' not found in action log",
            )

        if not entry.reversible:
            return UndoResult(
                action_id=action_id,
                success=False,
                message=f"Action '{action_id}' is marked non-reversible",
            )

        if entry.status == "undone":
            return UndoResult(
                action_id=action_id,
                success=False,
                message=f"Action '{action_id}' was already undone",
            )

        hint = entry.undo_hint or {}
        hint_type = hint.get("type")
        target_path = Path(entry.target)

        try:
            if hint_type == "delete_file":
                # Created file: undo deletes it
                if target_path.exists() and target_path.is_file():
                    target_path.unlink()
                msg = f"Deleted file created by action: {entry.target}"

            elif hint_type == "restore_file":
                # Modified file: restore previous content
                prev_content = hint.get("previous_content")
                if prev_content is not None:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    target_path.write_text(prev_content, encoding="utf-8")
                    msg = f"Restored previous file content for: {entry.target}"
                elif hint.get("backup_path"):
                    backup = Path(hint["backup_path"])
                    if backup.exists():
                        shutil.copy2(backup, target_path)
                        msg = f"Restored file from backup for: {entry.target}"
                    else:
                        return UndoResult(
                            action_id=action_id,
                            success=False,
                            message=f"Backup file not found: {hint.get('backup_path')}",
                        )
                else:
                    return UndoResult(
                        action_id=action_id,
                        success=False,
                        message="Missing previous_content or backup_path in undo hint",
                    )

            elif hint_type == "remove_dir":
                # Created directory: undo removes it
                if target_path.exists() and target_path.is_dir():
                    shutil.rmtree(target_path)
                msg = f"Removed directory created by action: {entry.target}"

            elif hint_type == "custom":
                msg = f"Custom undo processed for: {entry.target}"

            else:
                return UndoResult(
                    action_id=action_id,
                    success=False,
                    message=f"Unsupported undo hint type: '{hint_type}'",
                )

            # Record compensation event in append-only log
            self.log_action(
                tool_name="system_undo",
                action_type="undo_action",
                target=action_id,
                details={"original_target": entry.target, "hint_type": hint_type},
                reversible=False,
            )

            return UndoResult(
                action_id=action_id,
                success=True,
                message=msg,
            )

        except Exception as e:
            return UndoResult(
                action_id=action_id,
                success=False,
                message=f"Undo execution failed: {e}",
            )
