"""Redis-based message queue for inter-service communication."""

import json
from datetime import datetime
from typing import Any, Literal, overload

import redis.asyncio as redis

from shared.config import settings
from shared.queue.models import (
    AirtimeJobPayload,
    DataJobPayload,
    FlowEventPayload,
    PayoutJobPayload,
    ReceiptJobPayload,
    RefundJobPayload,
)


class RedisQueue:
    def __init__(self, redis_url: str = settings.redis_url):
        self.redis_url = redis_url
        self._redis: redis.Redis | None = redis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)

    async def connect(self) -> None:
        """No-op: Connection established in __init__."""
        if not self._redis:
            self._redis = redis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
        print(f"🔗 Connected to Redis: {self.redis_url}")

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()
            self._redis = None
            print("🔌 Disconnected from Redis")

    @overload
    async def enqueue(self, queue_name: Literal["banking:receipt_jobs"], message: ReceiptJobPayload) -> None: ...

    @overload
    async def enqueue(self, queue_name: Literal["banking:refunds"], message: RefundJobPayload) -> None: ...

    @overload
    async def enqueue(self, queue_name: Literal["banking:payouts"], message: PayoutJobPayload) -> None: ...

    @overload
    async def enqueue(
        self, queue_name: Literal["banking:transactions"], message: AirtimeJobPayload | DataJobPayload
    ) -> None: ...

    @overload
    async def enqueue(self, queue_name: Literal["banking:notifications"], message: dict[str, Any]) -> None: ...

    @overload
    async def enqueue(self, queue_name: Literal["banking:flow_events"], message: FlowEventPayload) -> None: ...

    @overload
    async def enqueue(self, queue_name: Literal["banking:messages"], message: dict[str, Any]) -> None: ...

    async def enqueue(self, queue_name: str, message: Any) -> None:  # type: ignore[misc]
        """Enqueue a message to the specified queue.

        Uses a List-based queue implementation (LPUSH).
        """
        assert self._redis, "Redis client not connected"

        enriched_message = {
            **message,
            "enqueued_at": datetime.utcnow().isoformat(),
        }

        list_name = f"{queue_name}:list"
        try:
            await self._redis.lpush(list_name, json.dumps(enriched_message))  # type: ignore[misc]
        except Exception as e:
            print(f"Error enqueuing message to {list_name}: {e}")
            raise

    async def enqueue_zset(self, queue_name: str, message: dict[str, Any], priority: int = 0) -> None:
        """Add a message to the queue (Sorted Set).

        Args:
            queue_name: Name of the queue (e.g., "banking:messages")
            message: Message payload as dictionary
            priority: Priority score (higher = more important)
        """
        if not self._redis:
            raise RuntimeError("Redis client not connected")

        enriched_message = {
            **message,
            "enqueued_at": datetime.utcnow().isoformat(),
        }

        await self._redis.zadd(queue_name, {json.dumps(enriched_message): priority})  # type: ignore[misc]

        print(f"📤 Enqueued message to {queue_name} (priority: {priority})")

    async def dequeue(self, queue_name: str, block_timeout: int = 5) -> dict[str, Any] | None:
        if not self._redis:
            raise RuntimeError("Redis client not connected")

        result = await self._redis.zpopmax(queue_name, count=1)  # type: ignore[misc]

        if result:
            message_json, priority = result[0]
            message = json.loads(message_json)
            return message

        return None

    async def dequeue_blocking(self, queue_name: str, timeout: int = 0) -> dict[str, Any] | None:
        """Dequeue a message from the queue, blocking until one is available or timeout."""
        if not self._redis:
            raise RuntimeError("Redis client not connected")

        list_name = f"{queue_name}:list"
        result = await self._redis.brpop(list_name, timeout=timeout)  # type: ignore[misc]

        if result:
            _, message_json = result
            message = json.loads(message_json)
            return message

        return None
