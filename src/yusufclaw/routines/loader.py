"""JSON configuration loader for YusufClaw routines.

Strictly validates:
- Valid JSON array containing at most 100 routines.
- Routine ID matches ^[a-zA-Z0-9_-]{1,64}$ and is unique across file.
- Valid 5-field UTC cron expression.
- Non-empty prompt <= 16,000 characters.
- Valid target channel (cli, telegram, web) with non-empty user and conversation IDs.
- Any error raises ValueError("Invalid routines configuration") without leaking sensitive content.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from yusufclaw.routines.cron import parse_cron
from yusufclaw.routines.models import Routine, RoutineTarget

ROUTINE_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
MAX_ROUTINES = 100
MAX_PROMPT_CHARS = 16_000


def _load_routines_impl(path: str | Path) -> list[Routine]:
    try:
        content = Path(path).read_text(encoding="utf-8")
        raw: Any = json.loads(content)
        if not isinstance(raw, list) or len(raw) > MAX_ROUTINES:
            raise ValueError()

        ids: set[str] = set()
        routines: list[Routine] = []

        for item in raw:
            if not isinstance(item, dict):
                raise ValueError()

            routine_id = item.get("id")
            if (
                not isinstance(routine_id, str)
                or not ROUTINE_ID_REGEX.match(routine_id)
                or routine_id in ids
            ):
                raise ValueError()
            ids.add(routine_id)

            cron = item.get("cron")
            prompt = item.get("prompt")
            if not isinstance(cron, str) or not isinstance(prompt, str):
                raise ValueError()

            clean_prompt = prompt.strip()
            if not clean_prompt or len(prompt) > MAX_PROMPT_CHARS:
                raise ValueError()

            # Validate cron expression
            parse_cron(cron)

            target_raw = item.get("target")
            if not isinstance(target_raw, dict):
                raise ValueError()

            channel = target_raw.get("channel")
            if channel not in ("cli", "telegram", "web"):
                raise ValueError()

            user_id = target_raw.get("userId") or target_raw.get("user_id")
            conv_id = target_raw.get("conversationId") or target_raw.get("conversation_id")

            if (
                not isinstance(user_id, str)
                or not user_id.strip()
                or not isinstance(conv_id, str)
                or not conv_id.strip()
            ):
                raise ValueError()

            target = RoutineTarget(
                channel=channel,
                user_id=user_id,
                conversation_id=conv_id,
            )
            routines.append(
                Routine(
                    id=routine_id,
                    cron=cron,
                    prompt=prompt,
                    target=target,
                )
            )

        return routines
    except Exception as exc:
        raise ValueError("Invalid routines configuration") from exc


def load_routines_sync(path: str | Path) -> list[Routine]:
    """Synchronously load and validate routines from JSON file."""
    return _load_routines_impl(path)


async def load_routines(path: str | Path) -> list[Routine]:
    """Asynchronously load and validate routines from JSON file."""
    return _load_routines_impl(path)


# CamelCase alias matching TypeScript core export
loadRoutines = load_routines

__all__ = [
    "MAX_PROMPT_CHARS",
    "MAX_ROUTINES",
    "loadRoutines",
    "load_routines",
    "load_routines_sync",
]
