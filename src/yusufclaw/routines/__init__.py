"""Routines subpackage for YusufClaw.

Implements scheduled routines and proactive agent tasks:
- Cron schedule parsing and trigger evaluation.
- Autonomous background tasks (nightly consolidation, health probes, digests).
- Queue-backed execution preventing duplicate concurrent routine runs.
- Routine execution logs and result reporting back to primary channel.

Note: Logic will be ported from TypeScript packages/core routines implementation.
"""

__all__: list[str] = []
