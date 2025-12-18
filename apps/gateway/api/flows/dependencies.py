"""Dependency factories for flow webhook handlers.

Note: All agent service calls are now handled via Redis queue events.
Gateway only needs redis queue and whatsapp client.
"""

from shared.clients.whatsapp_client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue
from shared.services.task_queue import TaskQueueService

from apps.gateway.core.config import settings


_redis_queue_instance = None


def get_redis_queue() -> RedisQueue:
    """Dependency factory for Redis queue with lazy initialization."""
    global _redis_queue_instance
    if _redis_queue_instance is None:
        _redis_queue_instance = RedisQueue(redis_url=settings.redis_url)
    return _redis_queue_instance


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
