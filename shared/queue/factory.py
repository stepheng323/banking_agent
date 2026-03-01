from shared.config.settings import settings
from shared.queue.adapter import QueuePublisher
from shared.queue.sns_adapter import SNSPublisher
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class QueuePublisherFactory:
    """Factory for creating AWS SNS queue publisher."""

    @staticmethod
    def get_publisher() -> QueuePublisher:
        """
        Return SNSPublisher for AWS-only deployment.
        """
        if not settings.aws_account_id:
            raise RuntimeError("AWS_ACCOUNT_ID is required for SNS queue publishing")
        logger.info("creating_sns_publisher", region=settings.aws_region)
        return SNSPublisher(
            region_name=settings.aws_region,
            account_id=settings.aws_account_id,
            project_name=settings.project_name,
            environment=settings.environment,
        )
