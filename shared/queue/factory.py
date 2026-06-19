
from shared.queue.adapter import QueuePublisher
from shared.queue.contracts import TopicType
from shared.queue.redis_stream_publisher import RedisStreamPublisher
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class CompositeQueuePublisher(QueuePublisher):
    """Route topics to the appropriate transport publisher."""

    CHAT_TOPICS: set[TopicType] = {"message.received", "flow_event.process"}

    def __init__(self, chat_publisher: QueuePublisher, async_publisher: QueuePublisher) -> None:
        self.chat_publisher = chat_publisher
        self.async_publisher = async_publisher

    async def publish(self, topic: TopicType, message: dict) -> None:
        if topic in self.CHAT_TOPICS:
            await self.chat_publisher.publish(topic, message)
            return
        await self.async_publisher.publish(topic, message)


class QueuePublisherFactory:
    """Factory for creating transport-routed queue publisher."""

    @staticmethod
    def _build_async_publisher() -> QueuePublisher:
        return RedisStreamPublisher()

    @staticmethod
    def get_publisher() -> QueuePublisher:
        """Return a composite publisher for chat ingress and async jobs."""
        return CompositeQueuePublisher(
            chat_publisher=RedisStreamPublisher(),
            async_publisher=QueuePublisherFactory._build_async_publisher(),
        )

    @staticmethod
    def get_async_publisher() -> QueuePublisher:
        """Return the Redis Streams publisher for async jobs only."""
        return QueuePublisherFactory._build_async_publisher()
