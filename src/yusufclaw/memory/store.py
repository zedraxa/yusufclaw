"""Typed structural contract for the existing sync/async memory operations."""

import builtins
import inspect
from collections.abc import Awaitable
from typing import Any, Protocol

from yusufclaw.memory.models import ListOptions, MemoryRecord, RememberInput, ReviseInput


async def resolve[T](value: T | Awaitable[T]) -> T:
    """Normalize SQLite values and Postgres awaitables without backend branching."""
    if inspect.isawaitable(value):
        return await value
    return value


class MemoryStore(Protocol):
    def list(
        self, scope: str, options: ListOptions | dict[str, Any] | None = None
    ) -> builtins.list[MemoryRecord] | Awaitable[builtins.list[MemoryRecord]]: ...

    def get(
        self, scope: str, id: str, include_inactive: bool = False
    ) -> MemoryRecord | None | Awaitable[MemoryRecord | None]: ...

    def remember(
        self, scope: str, input_data: RememberInput | dict[str, Any]
    ) -> MemoryRecord | Awaitable[MemoryRecord]: ...

    def revise(
        self, scope: str, id: str, input_data: ReviseInput | dict[str, Any]
    ) -> MemoryRecord | Awaitable[MemoryRecord]: ...

    def forget(self, scope: str, id: str, reason: str | None = None) -> bool | Awaitable[bool]: ...
