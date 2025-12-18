"""Task queue service package."""

from shared.cache.redis_client import RedisClient
from .service import TaskQueueService

__all__ = [
    "TaskQueueService",
    "task_queue_service",
]

# Singleton instance for easy import
_task_queue_service = None


def get_task_queue_service() -> TaskQueueService:
    """Get or create the task queue service singleton."""
    global _task_queue_service
    if _task_queue_service is None:
        _task_queue_service = TaskQueueService(
            redis_client=RedisClient.get_client()
        )
    return _task_queue_service


task_queue_service = get_task_queue_service()
