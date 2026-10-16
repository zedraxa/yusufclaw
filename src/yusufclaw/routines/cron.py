"""UTC 5-field cron parser for YusufClaw routines.

Supports:
- Numeric fields (minute 0-59, hour 0-23, day-of-month 1-31, month 1-12, day-of-week 0-7).
- Wildcards (*).
- Lists of values (e.g. 0,15,30,45).
- Inclusive ranges (e.g. 9-17, 1-5).
- Steps (e.g. */15, 1-5/2).
- Sunday alias: both 0 and 7 represent Sunday.
- Standard cron day matching semantics: restricted day-of-month and day-of-week fields are ORed.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime

FIELD_BOUNDS = [
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day of month
    (1, 12),  # month
    (0, 7),  # day of week (0 and 7 are Sunday)
]

ITEM_REGEX = re.compile(r"^(\*|\d+(?:-\d+)?)(?:/(\d+))?$")


def parse_cron(expression: str) -> Callable[[datetime], bool]:
    """Parse a 5-field UTC cron expression into a predicate function."""
    fields = expression.strip().split()
    if len(fields) != 5:
        raise ValueError("Cron requires five fields")

    sets: list[set[int]] = []
    for index, field in enumerate(fields):
        min_val, max_val = FIELD_BOUNDS[index]
        values: set[int] = set()

        for item in field.split(","):
            match = ITEM_REGEX.match(item)
            if not match:
                raise ValueError("Invalid cron field")

            target_range = match.group(1)
            step_str = match.group(2)

            if target_range == "*":
                start, end = min_val, max_val
            elif "-" in target_range:
                start_s, end_s = target_range.split("-")
                start, end = int(start_s), int(end_s)
            else:
                num = int(target_range)
                start = num
                end = max_val if step_str else num

            step = int(step_str) if step_str is not None else 1

            if start < min_val or end > max_val or start > end or step < 1 or step > max_val + 1:
                raise ValueError("Invalid cron range or step")

            for val in range(start, end + 1, step):
                # Day of week: 7 is alias for 0 (Sunday)
                if index == 4 and val == 7:
                    values.add(0)
                else:
                    values.add(val)

        sets.append(values)

    def matches(date: datetime) -> bool:
        # Normalize to UTC
        if date.tzinfo is None:
            utc_date = date.replace(tzinfo=UTC)
        else:
            utc_date = date.astimezone(UTC)

        # Python isoweekday: Monday=1, ..., Sunday=7
        # Convert to 0=Sunday, 1=Monday, ..., 6=Saturday
        dow = utc_date.isoweekday() % 7
        dom = utc_date.day

        day = dom in sets[2]
        weekday = dow in sets[4]

        # Standard cron: restricted day-of-month and day-of-week fields are ORed.
        if fields[2].startswith("*") or fields[4].startswith("*"):
            days = day and weekday
        else:
            days = day or weekday

        return (
            utc_date.minute in sets[0]
            and utc_date.hour in sets[1]
            and utc_date.month in sets[3]
            and days
        )

    return matches


# CamelCase alias matching TypeScript core export
parseCron = parse_cron

__all__ = ["parseCron", "parse_cron"]
