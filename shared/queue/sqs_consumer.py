"""SQS polling adapter used by ECS chat-critical consumers."""

import json
from typing import Any

import aioboto3

from shared.queue.adapter import QueueConsumer
from shared.queue.contracts import get_contract_by_queue_name
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class SQSQueueConsumer(QueueConsumer):
    """Queue consumer adapter that long-polls SQS and returns decoded payloads."""

    def __init__(self, region_name: str, account_id: str, project_name: str, environment: str):
        self.region_name = region_name
        self.account_id = account_id
        self.project_name = project_name
        self.environment = environment
        self.session = aioboto3.Session()
        self._queue_url_cache: dict[str, str] = {}

    def _build_queue_url(self, queue_name: str) -> str:
        contract = get_contract_by_queue_name(queue_name)
        if contract is None:
            raise ValueError(f"Unknown queue mapping: {queue_name}")

        aws_queue_name = f"{self.project_name}-{contract.sqs_queue_name}-{self.environment}"
        return f"https://sqs.{self.region_name}.amazonaws.com/{self.account_id}/{aws_queue_name}"

    def _get_queue_url(self, queue_name: str) -> str:
        url = self._queue_url_cache.get(queue_name)
        if url:
            return url
        url = self._build_queue_url(queue_name)
        self._queue_url_cache[queue_name] = url
        return url

    @staticmethod
    def _decode_body(body: str) -> dict[str, Any]:
        try:
            decoded = json.loads(body)
        except json.JSONDecodeError:
            return {}

        if isinstance(decoded, dict):
            # Backward-compatible SNS envelope support.
            message = decoded.get("Message")
            if isinstance(message, str):
                try:
                    return json.loads(message)
                except json.JSONDecodeError:
                    return {}
            return decoded

        return {}

    async def consume_one(self, queue_name: str, timeout: int = 5) -> dict[str, Any] | None:
        queue_url = self._get_queue_url(queue_name)
        wait_time = max(1, min(timeout, 20))

        async with self.session.client("sqs", region_name=self.region_name) as sqs:
            response = await sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=wait_time,
            )

            messages = response.get("Messages", [])
            if not messages:
                return None

            record = messages[0]
            receipt_handle = record.get("ReceiptHandle")
            body = record.get("Body", "{}")
            payload = self._decode_body(body)

            if receipt_handle:
                await sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)

            logger.info("sqs_message_consumed", queue_name=queue_name, queue_url=queue_url)
            return payload
