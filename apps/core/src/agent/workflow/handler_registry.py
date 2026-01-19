"""Workflow handler protocol and registry."""

from typing import Any, Protocol

from shared.types.planner import PlannedTask
from apps.core.src.agent.workflow.context import WorkflowContext
from apps.core.src.agent.workflow.models import TaskResult, TaskStatus


class WorkflowTaskHandler(Protocol):
    """Protocol for workflow task handlers.
    
    Each handler wraps an existing graph/service and exposes:
    - validate(): Check if task is ready (preflight)
    - execute(): Run the task
    """
    
    async def validate(self, task: PlannedTask, ctx: WorkflowContext) -> TaskResult | None:
        """Validate task is ready to execute (preflight check).
        
        Returns:
            None if ready to proceed
            TaskResult with NEEDS_INPUT if missing fields
            TaskResult with FAILED if validation fails
        """
        ...
    
    async def execute(self, task: PlannedTask, ctx: WorkflowContext) -> TaskResult:
        """Execute the task.
        
        Returns:
            TaskResult with COMPLETED, FAILED, or AWAITING_AUTH
        """
        ...


class UnsupportedExecutorResult:
    """Marker for unsupported executor detection."""
    
    def __init__(self, executor: str, instruction: str):
        self.executor = executor
        self.instruction = instruction
    
    def to_task_result(self, task_id: str) -> TaskResult:
        return TaskResult(
            task_id=task_id,
            status=TaskStatus.FAILED,
            error=f"'{self.executor}' is not supported yet",
            user_prompt=f"**{self.executor.title()}** isn't available yet.",
        )


class HandlerRegistry:
    """Registry mapping executor names to handlers."""
    
    def __init__(self) -> None:
        self._handlers: dict[str, WorkflowTaskHandler] = {}
    
    def register(self, executor: str, handler: WorkflowTaskHandler) -> None:
        """Register a handler for an executor."""
        self._handlers[executor] = handler
    
    def get(self, executor: str) -> WorkflowTaskHandler | UnsupportedExecutorResult:
        """Get handler for executor, or UnsupportedExecutorResult if not found."""
        handler = self._handlers.get(executor)
        if handler is None:
            return UnsupportedExecutorResult(executor, "")
        return handler
    
    def has(self, executor: str) -> bool:
        """Check if executor is registered."""
        return executor in self._handlers
    
    @property
    def supported_executors(self) -> list[str]:
        """List of supported executor names."""
        return list(self._handlers.keys())
