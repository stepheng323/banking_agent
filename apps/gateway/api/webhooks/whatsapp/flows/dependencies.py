"""Dependency factories for flow webhook handlers.

Note: All agent service calls are handled via queue events.
Gateway only needs queue publisher and WhatsApp client.
"""

from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.adapter import QueuePublisher
from shared.queue.factory import QueuePublisherFactory
from shared.services.task_queue import TaskQueueService

def get_queue_publisher() -> QueuePublisher:
    """Dependency factory for queue publisher."""
    return QueuePublisherFactory.get_publisher()


def get_whatsapp_client() -> WhatsAppClient:
    """
    Dependency factory for WhatsApp client.
    FastAPI will cache this dependency per request automatically.
    """
    return WhatsAppClient()


def get_task_queue_service() -> TaskQueueService:
    """Dependency factory for TaskQueueService (from shared)."""
    from shared.services.task_queue import task_queue_service

    return task_queue_service
