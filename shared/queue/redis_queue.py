"""Redis-based message queue for inter-service communication."""

import json
from datetime import datetime
from typing import Any, Dict, Optional

import redis.asyncio as redis


class RedisQueue:
    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self._redis: Optional[redis.Redis] = None

    async def connect(self) -> None:
        if not self._redis:
            self._redis = await redis.from_url(
                self.redis_url, encoding="utf-8", decode_responses=True
            )
            print(f"🔗 Connected to Redis: {self.redis_url}")

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()
            self._redis = None
            print("🔌 Disconnected from Redis")

    async def enqueue(self, queue_name: str, message: Dict[str, Any], priority: int = 0) -> None:
        """Add a message to the queue.

        Args:
            queue_name: Name of the queue (e.g., "banking:messages")
            message: Message payload as dictionary
            priority: Priority score (higher = more important)
        """
        if not self._redis:
            await self.connect()

        enriched_message = {
            **message,
            "enqueued_at": datetime.utcnow().isoformat(),
        }

        await self._redis.zadd(queue_name, {json.dumps(enriched_message): priority})

        print(f"📤 Enqueued message to {queue_name} (priority: {priority})")

    async def dequeue(self, queue_name: str, block_timeout: int = 5) -> Optional[Dict[str, Any]]:
        if not self._redis:
            await self.connect()

        result = await self._redis.zpopmax(queue_name, count=1)

        if result:
            message_json, priority = result[0]
            message = json.loads(message_json)
            return message

        return None

    async def dequeue_blocking(self, queue_name: str, timeout: int = 0) -> Optional[Dict[str, Any]]:
        """Dequeue a message from the queue, blocking until one is available or timeout."""
        if not self._redis:
            await self.connect()

        list_name = f"{queue_name}:list"
        result = await self._redis.brpop(list_name, timeout=timeout)

        if result:
            _, message_json = result
            message = json.loads(message_json)
            return message
        
        return None

    async def enqueue_simple(self, queue_name: str, message: Dict[str, Any]) -> None:
        if not self._redis:
            await self.connect()

        enriched_message = {
            **message,
            "enqueued_at": datetime.utcnow().isoformat(),
        }

        list_name = f"{queue_name}:list"
        try:
            await self._redis.lpush(list_name, json.dumps(enriched_message))
        except Exception as e:
            print(f"Error enqueuing message to {list_name}: {e}")
            raise

        if not self._redis:
            await self.connect()

        zset_len = await self._redis.zcard(queue_name) or 0
        list_len = await self._redis.llen(f"{queue_name}:list") or 0

        return zset_len + list_len
