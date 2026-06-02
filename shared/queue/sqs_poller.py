"""Async SQS polling helpers for long-lived VPS workers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import aioboto3

from shared.config.settings import settings
from shared.observability.events import emit_operational_event
from shared.queue.contracts import QueueContract
from shared.utils.logging import get_logger

logger = get_logger(__name__)

LambdaEventHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def build_physical_queue_name(contract: QueueContract) -> str:
    """Build the deployed SQS queue name from logical contract metadata."""
    if not contract.sqs_queue_name:
        raise ValueError(f"queue_contract_missing_sqs_queue_name topic={contract.logical_topic}")
    return f"{settings.project_name}-{contract.sqs_queue_name}-{settings.runtime.infrastructure_environment}"


class SQSPoller:
    """Poll one SQS queue and dispatch records using a Lambda-compatible async handler."""

    def __init__(self, contract: QueueContract, handler: LambdaEventHandler):
        self.contract = contract
        self.handler = handler
        self.queue_name = build_physical_queue_name(contract)

    async def run(self, stop_event: asyncio.Event) -> None:
        """Run the polling loop until stop is requested."""
        session = aioboto3.Session(region_name=settings.aws_region)
        async with session.client("sqs", region_name=settings.aws_region) as sqs:
            queue_url = await self._get_queue_url(sqs)
            queue_arn = await self._get_queue_arn(sqs, queue_url)
            logger.info(
                "sqs_poller_started",
                logical_topic=self.contract.logical_topic,
                queue_name=self.queue_name,
                queue_url=queue_url,
            )

            while not stop_event.is_set():
                response = await sqs.receive_message(
                    QueueUrl=queue_url,
                    MaxNumberOfMessages=settings.sqs_poll_max_messages,
                    WaitTimeSeconds=settings.sqs_wait_time_seconds,
                    VisibilityTimeout=settings.sqs_visibility_timeout_seconds,
                    MessageAttributeNames=["All"],
                    AttributeNames=["All"],
                )
                messages = response.get("Messages", [])
                if not messages:
                    continue

                event = {
                    "Records": [self._to_lambda_record(message, queue_arn) for message in messages],
                }
                result = await self.handler(event)
                failures = {
                    item.get("itemIdentifier")
                    for item in result.get("batchItemFailures", [])
                    if isinstance(item, dict) and item.get("itemIdentifier")
                }

                for message in messages:
                    message_id = message.get("MessageId")
                    if message_id in failures:
                        emit_operational_event(
                            "sqs_message_left_for_retry",
                            severity="warning",
                            domain="queue",
                            identifiers={
                                "logical_topic": self.contract.logical_topic,
                                "queue_name": self.queue_name,
                                "message_id": message_id,
                            },
                        )
                        logger.warning(
                            "sqs_message_left_for_retry",
                            logical_topic=self.contract.logical_topic,
                            queue_name=self.queue_name,
                            message_id=message_id,
                        )
                        continue
                    await sqs.delete_message(
                        QueueUrl=queue_url,
                        ReceiptHandle=message["ReceiptHandle"],
                    )

            logger.info("sqs_poller_stopped", logical_topic=self.contract.logical_topic, queue_name=self.queue_name)

    async def _get_queue_url(self, sqs: Any) -> str:
        response = await sqs.get_queue_url(QueueName=self.queue_name)
        queue_url = response.get("QueueUrl")
        if not isinstance(queue_url, str) or not queue_url:
            raise RuntimeError(f"sqs_queue_url_not_found queue_name={self.queue_name}")
        return queue_url

    @staticmethod
    async def _get_queue_arn(sqs: Any, queue_url: str) -> str:
        response = await sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])
        attributes = response.get("Attributes", {})
        queue_arn = attributes.get("QueueArn")
        if not isinstance(queue_arn, str) or not queue_arn:
            raise RuntimeError(f"sqs_queue_arn_not_found queue_url={queue_url}")
        return queue_arn

    @staticmethod
    def _to_lambda_record(message: dict[str, Any], queue_arn: str) -> dict[str, Any]:
        return {
            "messageId": message.get("MessageId"),
            "receiptHandle": message.get("ReceiptHandle"),
            "body": message.get("Body", "{}"),
            "attributes": message.get("Attributes", {}),
            "messageAttributes": message.get("MessageAttributes", {}),
            "eventSourceArn": queue_arn,
            "eventSource": "aws:sqs",
            "awsRegion": settings.aws_region,
        }
