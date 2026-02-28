import json

import aioboto3

from shared.queue.adapter import QueuePublisher, TopicType
from shared.queue.contracts import CONTRACT_BY_TOPIC
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class SNSPublisher(QueuePublisher):
    """AWS SNS implementation of QueuePublisher using aioboto3."""

    def __init__(
        self,
        region_name: str,
        account_id: str,
        project_name: str,
        environment: str,
    ):
        self.region_name = region_name
        self.account_id = account_id
        self.project_name = project_name
        self.environment = environment
        self.session = aioboto3.Session()

    def _get_topic_arn(self, topic: TopicType) -> str:
        """Construct SNS topic ARN from explicit queue contract mapping."""
        contract = CONTRACT_BY_TOPIC.get(topic)
        if contract is None:
            raise ValueError(f"Unsupported logical topic: {topic}")
        topic_name = contract.sns_topic_name
        return f"arn:aws:sns:{self.region_name}:{self.account_id}:{self.project_name}-{topic_name}-{self.environment}"

    async def publish(self, topic: TopicType, message: dict) -> None:
        """Publishes the message to the corresponding SNS topic."""
        topic_arn = self._get_topic_arn(topic)

        try:
            async with self.session.client("sns", region_name=self.region_name) as sns:
                response = await sns.publish(
                    TopicArn=topic_arn,
                    Message=json.dumps(message),
                )
                logger.info(
                    "message_published_sns",
                    topic=topic,
                    topic_arn=topic_arn,
                    message_id=response.get("MessageId"),
                )
        except Exception as e:
            logger.error(
                "sns_publish_failed",
                topic=topic,
                topic_arn=topic_arn,
                error=str(e),
                exc_info=True,
            )
            raise
