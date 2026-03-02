from typing import Any, Protocol

from shared.queue.contracts import TopicType


class QueuePublisher(Protocol):
    async def publish(self, topic: TopicType, message: dict[str, Any]) -> None: ...  # pragma: no cover
