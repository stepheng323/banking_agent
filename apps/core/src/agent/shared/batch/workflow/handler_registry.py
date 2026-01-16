"""Handler registry and protocol for pluggable task handlers."""

from typing import Protocol, runtime_checkable

from shared.types.planner import PlannedTask

from .models import TaskResult
from .workflow_context import WorkflowContext


@runtime_checkable
class WorkflowTaskHandler(Protocol):
    """Protocol for task execution handlers."""

    async def execute(self, task: PlannedTask, context: WorkflowContext) -> TaskResult:
        """
        Execute a single task.

        Args:
            task: The planned task to execute
            context: Workflow execution context with services and accumulated results

        Returns:
            TaskResult with execution outcome
        """
        ...

    async def rollback(self, task: PlannedTask, context: WorkflowContext) -> None:
        """
        Rollback a task if supported (optional).

        Args:
            task: The planned task to rollback
            context: Workflow execution context
        """
        ...

    def can_rollback(self) -> bool:
        """Whether this handler supports rollback."""
        ...


class WorkflowHandlerRegistry:
    """Registry for mapping executor types to handlers."""

    def __init__(self) -> None:
        self._handlers: dict[str, WorkflowTaskHandler] = {}

    def register(self, executor_type: str, handler: WorkflowTaskHandler) -> None:
        """
        Register a handler for an executor type.

        Args:
            executor_type: The executor type (e.g., "transfer", "airtime")
            handler: Handler implementing WorkflowTaskHandler protocol
        """
        self._handlers[executor_type] = handler

    def get(self, executor_type: str) -> WorkflowTaskHandler | None:
        """
        Get handler for an executor type.

        Args:
            executor_type: The executor type

        Returns:
            Handler or None if not registered
        """
        return self._handlers.get(executor_type)

    def has(self, executor_type: str) -> bool:
        """Check if handler exists for executor type."""
        return executor_type in self._handlers

    def list_types(self) -> list[str]:
        """List all registered executor types."""
        return list(self._handlers.keys())
