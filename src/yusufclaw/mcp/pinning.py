"""Server pinning and quarantine management for YusufClaw MCP subsystem.

Guards against MCP tool description poisoning and rogue servers (RESEARCH-ASIST YC-16,
MCPTox, ClawSafety) by enforcing:
- SHA-256 hashing of canonical tool names, descriptions, and input schemas.
- Pinning servers by version and content hash.
- Automatic quarantine of unpinned or changed servers until explicit operator approval.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yusufclaw.mcp.models import QuarantineStatus, ServerPinRecord


def canonical_tool_representation(tool: Any) -> dict[str, Any]:
    """Extract a normalized dictionary representation of an MCP tool for hashing."""
    if hasattr(tool, "model_dump"):
        data = tool.model_dump(by_alias=True)
    elif isinstance(tool, dict):
        data = dict(tool)
    else:
        data = {
            "name": getattr(tool, "name", ""),
            "description": getattr(tool, "description", "") or "",
            "input_schema": getattr(tool, "input_schema", {})
            or getattr(tool, "inputSchema", {}),
        }

    return {
        "name": str(data.get("name", "")),
        "description": str(data.get("description", "") or ""),
        "input_schema": data.get("input_schema") or data.get("inputSchema") or {},
    }


def compute_tools_hash(tools: list[Any]) -> str:
    """Compute deterministic SHA-256 hash of a list of MCP tools."""
    normalized = [canonical_tool_representation(t) for t in tools]
    # Sort deterministically by tool name
    normalized.sort(key=lambda t: t["name"])
    raw_json = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()


class PinningStore:
    """Persistent or in-memory store tracking pinned server hashes and quarantine states."""

    def __init__(self, lock_path: str | Path | None = None) -> None:
        self.lock_path = Path(lock_path) if lock_path else None
        self._records: dict[str, ServerPinRecord] = {}
        if self.lock_path and self.lock_path.exists():
            self.load()

    def load(self) -> None:
        """Load lock records from file."""
        if not self.lock_path or not self.lock_path.exists():
            return
        content = self.lock_path.read_text(encoding="utf-8")
        data = json.loads(content)
        if isinstance(data, dict):
            for s_name, rec_data in data.items():
                if isinstance(rec_data, dict):
                    self._records[s_name] = ServerPinRecord.model_validate(rec_data)

    def save(self) -> None:
        """Save lock records to file."""
        if not self.lock_path:
            return
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        serialized = {k: v.model_dump() for k, v in self._records.items()}
        self.lock_path.write_text(json.dumps(serialized, indent=2), encoding="utf-8")

    def get_record(self, server_name: str) -> ServerPinRecord | None:
        """Retrieve pin record for a server if exists."""
        return self._records.get(server_name)

    def check_server(
        self,
        server_name: str,
        current_hash: str,
        declared_version: str | None = None,
        config_pinned_hash: str | None = None,
    ) -> tuple[bool, QuarantineStatus, str | None]:
        """Check server against lock records and config.

        Returns:
            (is_allowed, status, reason)
        """
        rec = self._records.get(server_name)

        # Expected hash can come from lockfile or config
        expected_hash = rec.tools_hash if rec else config_pinned_hash

        if rec is not None and rec.status == "quarantined":
            return False, "quarantined", rec.quarantine_reason or "Server explicitly quarantined"

        if expected_hash is None:
            # Unpinned new server -> quarantine
            return (
                False,
                "unverified",
                "New unpinned MCP server discovered; quarantined until approved",
            )

        if expected_hash != current_hash:
            # Hash mismatch -> tamper/poisoning detected -> quarantine
            return (
                False,
                "quarantined",
                f"Tool hash mismatch: expected {expected_hash[:12]}, got {current_hash[:12]} "
                f"(potential tool poisoning attack)",
            )

        return True, "active", None

    def pin_server(
        self,
        server_name: str,
        tools_hash: str,
        version: str | None = None,
    ) -> ServerPinRecord:
        """Approve and pin a server, marking it active."""
        now = datetime.now(UTC).isoformat()
        rec = ServerPinRecord(
            server_name=server_name,
            version=version,
            tools_hash=tools_hash,
            status="active",
            quarantine_reason=None,
            updated_at=now,
        )
        self._records[server_name] = rec
        self.save()
        return rec

    def quarantine_server(
        self,
        server_name: str,
        reason: str,
        tools_hash: str = "",
        version: str | None = None,
    ) -> ServerPinRecord:
        """Quarantine a server, preventing tool execution."""
        now = datetime.now(UTC).isoformat()
        existing = self._records.get(server_name)
        h = tools_hash or (existing.tools_hash if existing else "")
        v = version or (existing.version if existing else None)
        rec = ServerPinRecord(
            server_name=server_name,
            version=v,
            tools_hash=h,
            status="quarantined",
            quarantine_reason=reason,
            updated_at=now,
        )
        self._records[server_name] = rec
        self.save()
        return rec

    def is_quarantined(self, server_name: str) -> bool:
        """Return True if server is quarantined or unverified."""
        rec = self._records.get(server_name)
        if rec is None:
            return False
        return rec.status != "active"
