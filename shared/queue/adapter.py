from typing import Any, Protocol

from shared.queue.contracts import TopicType


class QueuePublisher(Protocol):
    async def publish(self, topic: TopicType, message: dict[str, Any]) -> None: ...  # pragma: no cover


class QueueConsumer(Protocol):
    async def consume_one(
        self, queue_name: str, timeout: int = 5
    ) -> tuple[dict[str, Any], Any] | None: ...  # pragma: no cover

    async def ack_message(self, queue_name: str, receipt_handle: Any) -> None: ...  # pragma: no cover
