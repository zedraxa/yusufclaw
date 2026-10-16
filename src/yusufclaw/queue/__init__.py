"""Queue subpackage for YusufClaw.

Implements asynchronous message queue and background task dispatching using Redis:
- Task producer/consumer patterns for background subagent workflows.
- Routine execution queue with lock deduplication.
- Pub/Sub message broker for real-time channel notifications.
"""
from __future__ import annotations

import redis.asyncio as aioredis


class RedisQueue:
    """Lightweight Redis list-backed FIFO queue."""

    def __init__(self, client: aioredis.Redis[str], queue_name: str = "yusufclaw:tasks") -> None:
        self.client = client
        self.queue_name = queue_name

    async def push(self, item: str) -> int:
        """Push a message item to the tail of the queue."""
        result: int = await self.client.rpush(self.queue_name, item)
        return result

    async def pop(self, timeout: int = 0) -> str | None:
        """Pop a message item from the head of the queue (blocking)."""
        res = await self.client.blpop(self.queue_name, timeout=timeout)
        if res is None:
            return None
        _, value = res
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    async def length(self) -> int:
        """Return the current queue depth."""
        result: int = await self.client.llen(self.queue_name)
        return result


def get_redis_client(redis_url: str = "redis://127.0.0.1:6379/0") -> aioredis.Redis[str]:
    """Create and return an async Redis client instance."""
    return aioredis.from_url(redis_url, decode_responses=True)


__all__ = ["RedisQueue", "get_redis_client"]
