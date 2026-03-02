"""Consolidated Lambda handler for async transaction-domain queues."""

from typing import Any

from apps.core.src.runtime.transaction_worker_dependencies import setup_transaction_worker_consumers
from shared.queue.lambda_base import BaseSQSHandler
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransactionWorkerLambdaHandler(BaseSQSHandler):
    """Routes transaction-domain jobs to the correct consumer based on queue contract."""

    async def process_record(self, payload: dict, deps: Any) -> None:
        transaction_consumer, funding_consumer, payout_consumer, refund_consumer = deps

        context = self.get_active_record_context()
        domain = context.get("domain")
        queue_name = context.get("queue_name")
        message_id = context.get("message_id")

        if domain == "transaction":
            await transaction_consumer.process_transaction(payload)
        elif domain == "funding":
            await funding_consumer.process_job(payload)
        elif domain == "payout":
            await payout_consumer.process_job(payload)
        elif domain == "refund":
            await refund_consumer.process_job(payload)
        else:
            raise ValueError(f"transaction_worker_unknown_route domain={domain} queue_name={queue_name}")

        logger.info(
            "worker_record_processed",
            worker_name="transaction-worker",
            queue_name=queue_name,
            domain=domain,
            message_id=message_id,
        )


_handler = TransactionWorkerLambdaHandler(
    name="transaction_worker",
    dependency_loader=setup_transaction_worker_consumers,
)


def handler(event: dict, context: Any) -> dict:
    return _handler.handle(event, context)
