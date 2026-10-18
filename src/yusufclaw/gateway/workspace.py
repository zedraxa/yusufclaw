"""Operational workspace APIs backing UX Sprint 3 surfaces.

Provides offline-friendly helpers for:
- UX-08 automation / routines catalog with next-due timestamps
- UX-11 MCP connector hub status (pin / quarantine)
- UX-12 A2A teammate tree snapshots
- UX-13 monthly usage meter against the $50 ceiling
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from yusufclaw.a2a.hook import TeammateStatusHook
from yusufclaw.a2a.registry import TeammateRegistry
from yusufclaw.mcp.models import MCPConfig
from yusufclaw.mcp.pinning import PinningStore
from yusufclaw.routines.cron import parse_cron
from yusufclaw.routines.models import Routine, RoutineTarget

PROTOCOL_AG_UI = "ag-ui/1.0"
PROTOCOL_A2A = "a2a/1.0"
PROTOCOL_MCP = "mcp/2025-03-26"
MONTHLY_LIMIT_USD = 50.0
ROUTINE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
ROUTINES_STATE_KEY = "workspace:routines:v1"
BUDGET_STATE_PREFIX = "budget:monthly:"


def next_cron_fire(
    cron: str,
    *,
    after: datetime | None = None,
    horizon_minutes: int = 60 * 24 * 14,
) -> str | None:
    """Return the next UTC ISO timestamp matching a 5-field cron expression."""
    matcher = parse_cron(cron)
    start = after or datetime.now(UTC)
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    else:
        start = start.astimezone(UTC)
    cursor = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(max(1, horizon_minutes)):
        if matcher(cursor):
            return cursor.isoformat()
        cursor += timedelta(minutes=1)
    return None


@dataclass
class UsageMeter:
    """Turn/session/monthly spend tracker for the header telemetry gauge (UX-13)."""

    monthly_limit_usd: float = MONTHLY_LIMIT_USD
    turn_prompt_tokens: int = 0
    turn_completion_tokens: int = 0
    turn_cost_usd: float = 0.0
    session_tokens_total: int = 0
    session_cost_usd: float = 0.0
    monthly_spend_usd: float = 0.0
    memory: Any | None = None
    prompt_cost_per_1m: float = 3.0
    completion_cost_per_1m: float = 15.0

    def _month_key(self, when: datetime | None = None) -> str:
        stamp = when or datetime.now(UTC)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return f"{BUDGET_STATE_PREFIX}{stamp.astimezone(UTC).strftime('%Y-%m')}"

    async def load_monthly(self) -> None:
        """Hydrate monthly spend from durable memory state when available."""
        if self.memory is None:
            return
        key = self._month_key()
        getter = getattr(self.memory, "get_state", None) or getattr(self.memory, "getState", None)
        if getter is None:
            return
        raw = getter(key)
        if hasattr(raw, "__await__"):
            raw = await raw
        if not raw:
            return
        try:
            self.monthly_spend_usd = max(0.0, float(raw))
        except (TypeError, ValueError):
            self.monthly_spend_usd = 0.0

    async def _persist_monthly(self) -> None:
        if self.memory is None:
            return
        key = self._month_key()
        setter = getattr(self.memory, "set_state", None) or getattr(self.memory, "setState", None)
        if setter is None:
            return
        res = setter(key, f"{self.monthly_spend_usd:.6f}")
        if hasattr(res, "__await__"):
            await res

    def begin_turn(self) -> None:
        """Reset per-turn counters."""
        self.turn_prompt_tokens = 0
        self.turn_completion_tokens = 0
        self.turn_cost_usd = 0.0

    async def record_tokens(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Accumulate token usage and dollar cost; persist monthly spend."""
        prompt = max(0, int(prompt_tokens))
        completion = max(0, int(completion_tokens))
        cost = (prompt / 1_000_000.0) * self.prompt_cost_per_1m + (
            completion / 1_000_000.0
        ) * self.completion_cost_per_1m
        self.turn_prompt_tokens += prompt
        self.turn_completion_tokens += completion
        self.turn_cost_usd += cost
        self.session_tokens_total += prompt + completion
        self.session_cost_usd += cost
        self.monthly_spend_usd += cost
        await self._persist_monthly()
        return cost

    def snapshot(self) -> dict[str, Any]:
        """AG-UI meter.usage payload."""
        limit = max(0.01, self.monthly_limit_usd)
        pct = min(100.0, (self.monthly_spend_usd / limit) * 100.0)
        level = "green"
        if self.monthly_spend_usd >= 45.0:
            level = "red"
        elif self.monthly_spend_usd >= 35.0:
            level = "yellow"
        return {
            "protocolVersion": PROTOCOL_AG_UI,
            "eventType": "meter.usage",
            "turnTokens": {
                "prompt": self.turn_prompt_tokens,
                "completion": self.turn_completion_tokens,
                "total": self.turn_prompt_tokens + self.turn_completion_tokens,
            },
            "turnCostUsd": round(self.turn_cost_usd, 6),
            "sessionTokensTotal": self.session_tokens_total,
            "sessionCostUsd": round(self.session_cost_usd, 6),
            "monthlySpendUsd": round(self.monthly_spend_usd, 6),
            "monthlyLimitUsd": self.monthly_limit_usd,
            "budgetPercent": round(pct, 2),
            "budgetLevel": level,
            "subagentsPaused": self.monthly_spend_usd >= 45.0,
        }


@dataclass
class AutomationStore:
    """Memory-backed routine catalog for the automation dashboard (UX-08)."""

    memory: Any | None = None
    seed: list[Routine] = field(default_factory=list)

    async def _read_raw(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for r in self.seed:
            items.append(
                {
                    "id": r.id,
                    "cron": r.cron,
                    "prompt": r.prompt,
                    "target": r.target.to_dict(),
                    "source": "seed",
                }
            )
        if self.memory is None:
            return items
        getter = getattr(self.memory, "get_state", None) or getattr(self.memory, "getState", None)
        if getter is None:
            return items
        raw = getter(ROUTINES_STATE_KEY)
        if hasattr(raw, "__await__"):
            raw = await raw
        if not raw:
            return items
        try:
            stored = json.loads(raw)
        except Exception:
            return items
        if not isinstance(stored, list):
            return items
        seen = {i["id"] for i in items}
        for entry in stored:
            if isinstance(entry, dict) and entry.get("id") not in seen:
                entry.setdefault("source", "user")
                items.append(entry)
                seen.add(str(entry.get("id")))
        return items

    async def _write_user_routines(self, routines: list[dict[str, Any]]) -> None:
        if self.memory is None:
            return
        setter = getattr(self.memory, "set_state", None) or getattr(self.memory, "setState", None)
        if setter is None:
            return
        user_only = [r for r in routines if r.get("source") != "seed"]
        res = setter(ROUTINES_STATE_KEY, json.dumps(user_only))
        if hasattr(res, "__await__"):
            await res

    async def list_definitions(self) -> list[dict[str, Any]]:
        """Return routine definitions with verified next-due timestamps."""
        out: list[dict[str, Any]] = []
        for item in await self._read_raw():
            cron = str(item.get("cron", ""))
            try:
                next_due = next_cron_fire(cron)
            except ValueError:
                next_due = None
            grants = item.get("grants") or item.get("allowedTools") or []
            if not isinstance(grants, list):
                grants = []
            out.append(
                {
                    "id": item.get("id"),
                    "cron": cron,
                    "prompt": item.get("prompt"),
                    "target": item.get("target"),
                    "grants": grants,
                    "misfirePolicy": item.get("misfirePolicy") or item.get("misfire_policy") or "skip",
                    "nextDueAt": next_due,
                    "source": item.get("source", "user"),
                    "protocolVersion": PROTOCOL_AG_UI,
                }
            )
        return out

    async def create(
        self,
        *,
        routine_id: str,
        cron: str,
        prompt: str,
        channel: str = "web",
        user_id: str = "local",
        conversation_id: str = "default",
        grants: list[str] | None = None,
        misfire_policy: str = "skip",
    ) -> dict[str, Any]:
        """Validate and persist a new user routine."""
        if not ROUTINE_ID_RE.match(routine_id):
            raise ValueError("Invalid routine id")
        clean_prompt = prompt.strip()
        if not clean_prompt or len(prompt) > 16_000:
            raise ValueError("Invalid routine prompt")
        if channel not in ("cli", "telegram", "web"):
            raise ValueError("Invalid target channel")
        if misfire_policy not in ("skip", "coalesce", "catchup"):
            raise ValueError("Invalid misfire policy")
        parse_cron(cron)  # validate
        existing = await self._read_raw()
        if any(str(e.get("id")) == routine_id for e in existing):
            raise ValueError("Routine id already exists")
        entry = {
            "id": routine_id,
            "cron": cron.strip(),
            "prompt": prompt,
            "target": {
                "channel": channel,
                "userId": user_id,
                "conversationId": conversation_id,
            },
            "grants": list(grants or []),
            "misfirePolicy": misfire_policy,
            "source": "user",
        }
        existing.append(entry)
        await self._write_user_routines(existing)
        defs = await self.list_definitions()
        for d in defs:
            if d["id"] == routine_id:
                return d
        return entry

    async def delete(self, routine_id: str) -> bool:
        """Remove a user-authored routine (seed routines cannot be deleted)."""
        items = await self._read_raw()
        kept: list[dict[str, Any]] = []
        removed = False
        for item in items:
            if str(item.get("id")) == routine_id and item.get("source") != "seed":
                removed = True
                continue
            kept.append(item)
        if removed:
            await self._write_user_routines(kept)
        return removed


def serialize_connectors(
    config: MCPConfig | None,
    pinning: PinningStore | None,
) -> list[dict[str, Any]]:
    """Build MCP connector hub rows (UX-11)."""
    cfg = config or MCPConfig()
    pins = pinning or PinningStore()
    rows: list[dict[str, Any]] = []
    for name, server in cfg.servers.items():
        rec = pins.get_record(name)
        status = "quarantined" if server.quarantined else "connected"
        if rec is not None:
            status = {
                "active": "connected",
                "quarantined": "quarantined",
                "unverified": "error",
            }.get(rec.status, status)
        elif not server.pinned_hash:
            status = "error"
        rows.append(
            {
                "connectorId": name,
                "name": server.name,
                "transport": server.transport,
                "status": status,
                "protocolVersion": PROTOCOL_MCP,
                "oauth": bool(getattr(server, "oauth_client_id", None) or server.url),
                "pinnedHash": (rec.tools_hash if rec else server.pinned_hash) or "",
                "version": (rec.version if rec else server.version) or "",
                "quarantineReason": rec.quarantine_reason if rec else None,
                "enabled": server.enabled,
                "toolCount": 0,
                "eventType": "connector.status_changed",
            }
        )
    return rows


def serialize_teammates(
    registry: TeammateRegistry | None,
    hook: TeammateStatusHook | None,
) -> dict[str, Any]:
    """Build A2A teammate hierarchy snapshot (UX-12)."""
    cards: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    if registry is not None:
        for card in registry.list_teammates():
            payload = card.model_dump(by_alias=True)
            payload.setdefault("protocolVersion", PROTOCOL_A2A)
            cards.append(payload)
        for task in registry.list_active_tasks():
            active.append(
                {
                    "taskId": task.task_id,
                    "agentId": task.agent_id,
                    "parentTaskId": task.parent_task_id,
                    "depth": task.depth,
                    "status": task.status,
                    "maxCostUsd": task.budget.max_cost_usd,
                    "protocolVersion": PROTOCOL_A2A,
                }
            )
    history: list[dict[str, Any]] = []
    if hook is not None:
        for ev in hook.history[-50:]:
            history.append(ev.model_dump(by_alias=True, mode="json"))
    return {
        "protocolVersion": PROTOCOL_A2A,
        "maxDepth": 1,
        "maxConcurrentWorkers": getattr(registry, "max_concurrent_workers", 3) if registry else 3,
        "cards": cards,
        "active": active,
        "recentEvents": history,
        "eventType": "teammate.tree",
    }


def validate_routine_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a create-routine JSON body."""
    routine_id = str(raw.get("id") or raw.get("routineId") or "").strip()
    cron = str(raw.get("cron") or "").strip()
    prompt = str(raw.get("prompt") or "")
    target_raw = raw.get("target")
    target: dict[str, Any] = target_raw if isinstance(target_raw, dict) else {}
    channel = str(target.get("channel") or raw.get("channel") or "web")
    user_id = str(
        target.get("userId") or target.get("user_id") or raw.get("userId") or "local"
    )
    conversation_id = str(
        target.get("conversationId")
        or target.get("conversation_id")
        or raw.get("conversationId")
        or "default"
    )
    grants_raw = raw.get("grants") or raw.get("allowedTools") or []
    grants = [str(g) for g in grants_raw] if isinstance(grants_raw, list) else []
    misfire = str(raw.get("misfirePolicy") or raw.get("misfire_policy") or "skip")
    return {
        "routine_id": routine_id,
        "cron": cron,
        "prompt": prompt,
        "channel": channel,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "grants": grants,
        "misfire_policy": misfire,
    }


def seed_routine(
    routine_id: str,
    cron: str,
    prompt: str,
    *,
    channel: str = "web",
    user_id: str = "local",
    conversation_id: str = "default",
) -> Routine:
    """Helper to build a seed Routine object."""
    return Routine(
        id=routine_id,
        cron=cron,
        prompt=prompt,
        target=RoutineTarget(
            channel=channel,  # type: ignore[arg-type]
            user_id=user_id,
            conversation_id=conversation_id,
        ),
    )


__all__ = [
    "PROTOCOL_A2A",
    "PROTOCOL_AG_UI",
    "PROTOCOL_MCP",
    "MONTHLY_LIMIT_USD",
    "AutomationStore",
    "UsageMeter",
    "next_cron_fire",
    "seed_routine",
    "serialize_connectors",
    "serialize_teammates",
    "validate_routine_payload",
]
