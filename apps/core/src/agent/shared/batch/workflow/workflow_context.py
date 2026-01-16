"""Workflow execution context holding state and services."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from shared.cache.redis_client import Redis
from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue
from shared.services.task_queue import TaskQueueService

from .models import TaskResult

if TYPE_CHECKING:
    from .handler_registry import WorkflowHandlerRegistry


@dataclass
class WorkflowContext:
    """Holds execution state and services for a workflow run."""

    # User identification
    phone_number: str
    user_id: str
    workflow_id: str

    # Auth state
    pin_verified: bool = False

    # Accumulated execution results (task_id -> TaskResult)
    task_results: dict[str, TaskResult] = field(default_factory=dict)

    # Services and clients
    redis_client: Redis | None = None
    queue: RedisQueue | None = None
    whatsapp_client: WhatsAppClient | None = None
    task_queue_service: TaskQueueService | None = None
    handler_registry: "WorkflowHandlerRegistry | None" = None

    # Additional service references for handlers
    services: dict[str, Any] = field(default_factory=dict)

    def get_result(self, task_id: str) -> TaskResult | None:
        """Get result for a specific task."""
        return self.task_results.get(task_id)

    def set_result(self, result: TaskResult) -> None:
        """Store result for a task."""
        self.task_results[result.task_id] = result

    def get_service(self, name: str) -> Any:
        """Get a service by name."""
        return self.services.get(name)

    def register_service(self, name: str, service: Any) -> None:
        """Register a service for handlers to use."""
        self.services[name] = service
