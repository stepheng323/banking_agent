from typing import Any, Protocol

from shared.queue.contracts import TopicType


class QueuePublisher(Protocol):
    async def publish(self, topic: TopicType, message: dict[str, Any]) -> None: ...  # pragma: no cover


class QueueConsumer(Protocol):
    async def consume_one(self, queue_name: str, timeout: int = 5) -> dict[str, Any] | None: ...  # pragma: no cover
