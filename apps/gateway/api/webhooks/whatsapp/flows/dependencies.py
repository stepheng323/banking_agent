"""Dependency factories for flow webhook handlers.

Note: All agent service calls are handled via queue events.
Gateway only needs queue publisher and WhatsApp client.
"""

from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.adapter import QueuePublisher
from shared.queue.factory import QueuePublisherFactory


def get_queue_publisher() -> QueuePublisher:
    """Dependency factory for queue publisher."""
    return QueuePublisherFactory.get_publisher()


def get_whatsapp_client() -> WhatsAppClient:
    """
    Dependency factory for WhatsApp client.
    FastAPI will cache this dependency per request automatically.
    """
    return WhatsAppClient()
