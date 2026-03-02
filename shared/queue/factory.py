from shared.config.settings import settings
from shared.queue.adapter import QueuePublisher
from shared.queue.contracts import TopicType
from shared.queue.redis_stream_publisher import RedisStreamPublisher
from shared.queue.sns_publisher import SNSPublisher
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


def _build_sns_topic_arn() -> str:
    """Construct the SNS topic ARN from settings."""
    return (
        f"arn:aws:sns:{settings.aws_region}:{settings.aws_account_id}:"
        f"{settings.project_name}-async-jobs-{settings.environment}"
    )


class QueuePublisherFactory:
    """Factory for creating transport-routed queue publisher."""

    @staticmethod
    def get_publisher() -> QueuePublisher:
        """Return a composite publisher for chat ingress and async jobs."""
        if not settings.aws_account_id:
            raise RuntimeError("AWS_ACCOUNT_ID is required for queue publishing")

        logger.info("creating_composite_queue_publisher", region=settings.aws_region)

        return CompositeQueuePublisher(
            chat_publisher=RedisStreamPublisher(),
            async_publisher=SNSPublisher(
                region_name=settings.aws_region,
                topic_arn=_build_sns_topic_arn(),
            ),
        )

    @staticmethod
    def get_async_publisher() -> QueuePublisher:
        """Return the SNS publisher for async jobs only."""
        if not settings.aws_account_id:
            raise RuntimeError("AWS_ACCOUNT_ID is required for queue publishing")
        return SNSPublisher(
            region_name=settings.aws_region,
            topic_arn=_build_sns_topic_arn(),
        )
