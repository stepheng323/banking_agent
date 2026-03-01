"""Dependency loader for messaging-domain Lambda worker."""

from apps.core.src.queue_consumers import OutboxConsumer
from apps.core.src.queue_consumers.actionable_consumer import ActionableMessageConsumer
from apps.core.src.runtime.common import build_messaging_clients, require_aws_account_id
from shared.queue.factory import QueuePublisherFactory


def setup_messaging_worker_consumers() -> tuple[OutboxConsumer, ActionableMessageConsumer]:
    """Setup async messaging-domain consumers owned by messaging Lambda worker."""
    require_aws_account_id()
    queue_publisher = QueuePublisherFactory.get_publisher()
    messaging_clients = build_messaging_clients()
    outbox_consumer = OutboxConsumer(
        publisher=queue_publisher,
        messaging_clients=messaging_clients,
    )
    actionable_consumer = ActionableMessageConsumer()
    return outbox_consumer, actionable_consumer

