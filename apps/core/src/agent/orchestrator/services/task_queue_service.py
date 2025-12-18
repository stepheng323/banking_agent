"""Task queue service - re-exported from shared for backward compatibility.

DEPRECATED: Import from shared.services.task_queue instead:
    from shared.services.task_queue import TaskQueueService, task_queue_service
"""

# Re-export from shared location
from shared.services.task_queue import TaskQueueService

__all__ = ["TaskQueueService"]
