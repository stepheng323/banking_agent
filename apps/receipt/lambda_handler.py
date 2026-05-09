"""Consolidated Lambda handler for async receipt-domain queues."""

from typing import Any

from apps.receipt.dependencies import setup_receipt_worker_consumers
from shared.queue.lambda_base import BaseSQSHandler
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ReceiptWorkerLambdaHandler(BaseSQSHandler):
    """Routes receipt-domain jobs based on queue contract."""

    async def process_record(self, payload: dict, deps: Any) -> None:
        receipt_consumer = deps

        context = self.get_active_record_context()
        domain = context.get("domain")
        queue_name = context.get("queue_name")
        message_id = context.get("message_id")

        if domain == "receipt":
            await receipt_consumer.process_job(payload)
        else:
            raise ValueError(f"receipt_worker_unknown_route domain={domain} queue_name={queue_name}")

        logger.info(
            "worker_record_processed",
            worker_name="receipt-worker",
            queue_name=queue_name,
            domain=domain,
            message_id=message_id,
        )


_handler = ReceiptWorkerLambdaHandler(name="receipt_worker", dependency_loader=setup_receipt_worker_consumers)


def handler(event: dict, context: Any) -> dict:
    return _handler.handle(event, context)
