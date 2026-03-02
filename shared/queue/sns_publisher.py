"""SNS publisher for async job topics (transaction, funding, payout, refund, receipt)."""

import json
from typing import Any

import aioboto3

from shared.queue.adapter import QueuePublisher
from shared.queue.contracts import TopicType, get_contract_by_topic


class SNSPublisher(QueuePublisher):
    """Publish async jobs to the consolidated SNS topic with domain attribute."""

    def __init__(self, region_name: str, topic_arn: str) -> None:
        self.region_name = region_name
        self.topic_arn = topic_arn
        self.session = aioboto3.Session()

    async def publish(self, topic: TopicType, message: dict[str, Any]) -> None:
        contract = get_contract_by_topic(topic)
        if not contract.domain:
            raise ValueError(f"Topic {topic} is not configured for SNS (no domain attribute)")

        async with self.session.client("sns", region_name=self.region_name) as sns:
            await sns.publish(
                TopicArn=self.topic_arn,
                Message=json.dumps(message),
                MessageAttributes={
                    "domain": {
                        "DataType": "String",
                        "StringValue": contract.domain,
                    },
                },
            )
