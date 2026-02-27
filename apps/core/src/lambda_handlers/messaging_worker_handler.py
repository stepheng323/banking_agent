"""Consolidated Lambda handler for async messaging-domain queues."""

from typing import Any

from apps.core.src.dependencies import setup_messaging_worker_consumers
from apps.receipt.src.consumer import ReceiptJobConsumer
from shared.queue.factory import QueuePublisherFactory
from shared.queue.lambda_base import BaseSQSHandler
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def setup_messaging_worker_dependencies() -> tuple[Any, ...]:
    """Load core consumers and append receipt job consumer for routing."""
    outbox_consumer, actionable_consumer = setup_messaging_worker_consumers()
    publisher = QueuePublisherFactory.get_publisher()
    receipt_consumer = ReceiptJobConsumer(queue_publisher=publisher)
    return (outbox_consumer, actionable_consumer, receipt_consumer)


class MessagingWorkerLambdaHandler(BaseSQSHandler):
    """Routes messaging-domain jobs to the correct consumer based on queue contract."""

    async def process_record(self, payload: dict, deps: Any) -> None:
        outbox_consumer, actionable_consumer, receipt_consumer = deps

        context = self.get_active_record_context()
        logical_topic = context.get("logical_topic")
        queue_name = context.get("queue_name")
        message_id = context.get("message_id")

        if logical_topic == "notification.send":
            await outbox_consumer.process_job(payload)
        elif logical_topic == "actionable_message.send":
            await actionable_consumer.process_job(payload)
        elif logical_topic == "receipt.process":
            await receipt_consumer.process_job(payload)
        else:
            raise ValueError(f"messaging_worker_unknown_route logical_topic={logical_topic} queue_name={queue_name}")

        logger.info(
            "worker_record_processed",
            worker_name="messaging-worker",
            queue_name=queue_name,
            logical_topic=logical_topic,
            message_id=message_id,
        )


_handler = MessagingWorkerLambdaHandler(name="messaging_worker", dependecy_loader=setup_messaging_worker_dependencies)


def handler(event: dict, context: Any) -> dict:
    return _handler.handle(event, context)
