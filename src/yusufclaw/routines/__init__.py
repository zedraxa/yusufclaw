"""Routines subpackage for YusufClaw.

Implements scheduled routines and proactive agent tasks:
- Cron schedule parsing and trigger evaluation.
- Autonomous background tasks (nightly consolidation, health probes, digests).
- Queue-backed execution preventing duplicate concurrent routine runs.
- Routine execution logs and result reporting back to primary channel.

Note: Logic ported from TypeScript packages/core and gateway routines implementations.
"""

from yusufclaw.routines.cron import parse_cron, parseCron
from yusufclaw.routines.loader import (
    MAX_PROMPT_CHARS,
    MAX_ROUTINES,
    load_routines,
    load_routines_sync,
    loadRoutines,
)
from yusufclaw.routines.models import (
    ChannelType,
    Routine,
    RoutineMessage,
    RoutineTarget,
)
from yusufclaw.routines.runner import (
    AbortController,
    AbortSignal,
    ChannelRunner,
    ChannelRunnerOptions,
    RoutineRunner,
    RoutineTransport,
    SingleFlightQueue,
    check_aborted,
    is_aborted,
    split_telegram_text,
)
from yusufclaw.routines.scheduler import (
    RoutineScheduler,
    create_routine_scheduler,
    createRoutineScheduler,
    get_memory_state,
    set_memory_state,
)

__all__ = [
    "MAX_PROMPT_CHARS",
    "MAX_ROUTINES",
    "AbortController",
    "AbortSignal",
    "ChannelRunner",
    "ChannelRunnerOptions",
    "ChannelType",
    "Routine",
    "RoutineMessage",
    "RoutineRunner",
    "RoutineScheduler",
    "RoutineTarget",
    "RoutineTransport",
    "SingleFlightQueue",
    "check_aborted",
    "createRoutineScheduler",
    "create_routine_scheduler",
    "get_memory_state",
    "is_aborted",
    "loadRoutines",
    "load_routines",
    "load_routines_sync",
    "parseCron",
    "parse_cron",
    "set_memory_state",
    "split_telegram_text",
]
