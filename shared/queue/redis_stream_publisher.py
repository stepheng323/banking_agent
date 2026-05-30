"""Redis Streams publisher for logical queue topics."""

from typing import Any

from shared.cache.redis_client import RedisClient
from shared.queue.adapter import QueuePublisher
from shared.queue.contracts import TopicType, get_contract_by_topic
from shared.utils.json import json_dumps_safe


class RedisStreamPublisher(QueuePublisher):
    """Publish queue messages to Redis Streams."""

    def __init__(self) -> None:
        self.redis = RedisClient.get_client()

    async def publish(self, topic: TopicType, message: dict[str, Any]) -> None:
        contract = get_contract_by_topic(topic)
        stream_name = contract.redis_stream_name
        if not stream_name:
            raise ValueError(f"Topic {topic} is not configured for Redis Streams")

        await self.redis.xadd(
            stream_name,
            {
                "topic": topic,
                "payload": json_dumps_safe(message),
            },
            maxlen=10000,
            approximate=True,
        )
