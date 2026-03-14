"""No-op queue publisher used for passive/disabled runtimes."""

from typing import Any

from shared.queue.adapter import QueuePublisher
from shared.queue.contracts import TopicType
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class NoOpQueuePublisher(QueuePublisher):
    """Queue publisher that logs and drops messages."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def publish(self, topic: TopicType, message: dict[str, Any]) -> None:
        logger.info(
            "queue_publish_skipped",
            topic=topic,
            reason=self.reason,
            message_keys=sorted(message.keys()),
        )
