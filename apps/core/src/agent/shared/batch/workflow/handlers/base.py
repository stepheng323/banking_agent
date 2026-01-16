"""Base handler with common logic for all task types."""

import uuid
from abc import ABC, abstractmethod
from typing import Any

from shared.types.agent_types import TaskStatus
from shared.types.planner import PlannedTask

from ..models import ErrorKind, TaskResult
from ..workflow_context import WorkflowContext


class BaseTaskHandler(ABC):
    """Abstract base handler providing common functionality."""

    @abstractmethod
    async def execute(self, task: PlannedTask, context: WorkflowContext) -> TaskResult:
        """Execute the task. Must be implemented by subclasses."""
        ...

    async def rollback(self, task: PlannedTask, context: WorkflowContext) -> None:  # noqa: B027
        """Rollback is not supported by default. Override in subclass if needed."""
        pass

    def can_rollback(self) -> bool:
        """Whether this handler supports rollback."""
        return False

    def _create_success_result(
        self,
        task: PlannedTask,
        data: dict[str, Any] | None = None,
        provider_ref: str | None = None,
    ) -> TaskResult:
        """Create a successful task result."""
        return TaskResult(
            task_id=task.task_id,
            run_id=str(uuid.uuid4()),
            status=TaskStatus.COMPLETED,
            provider_ref=provider_ref,
            data=data or {},
        )

    def _create_failure_result(
        self,
        task: PlannedTask,
        error_message: str,
        error_kind: ErrorKind = ErrorKind.UNKNOWN,
        error_code: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> TaskResult:
        """Create a failed task result."""
        return TaskResult(
            task_id=task.task_id,
            run_id=str(uuid.uuid4()),
            status=TaskStatus.FAILED,
            error_message=error_message,
            error_kind=error_kind,
            error_code=error_code,
            data=data or {},
        )

    def _get_idempotency_key(self, task: PlannedTask, context: WorkflowContext) -> str:
        """Generate idempotency key for the task."""
        if task.idempotency_key:
            return task.idempotency_key
        return f"{context.user_id}:{context.workflow_id}:{task.task_id}"
