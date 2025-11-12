"""Dependency factories for flow webhook handlers."""

from shared.clients.whatsapp_client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue
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

