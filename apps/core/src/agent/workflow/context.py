"""Workflow execution context.

Carries user state, authentication status, and task results through the workflow.
"""

from typing import Any

from pydantic import BaseModel, Field, ConfigDict

from apps.core.src.agent.workflow.models import TaskResult


class WorkflowContext(BaseModel):
    """Context passed through workflow execution."""
    
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    # User identification
    user_id: str
    phone_number: str
    
    # Authentication state
    pin_verified: bool = False
    
    # Task execution results (task_id → result)
    task_results: dict[str, TaskResult] = Field(default_factory=dict)
    
    # User profile and accounts (hydrated at start)
    user_profile: dict[str, Any] = Field(default_factory=dict)
    accounts: list[dict] = Field(default_factory=list)
    beneficiaries: list[dict] = Field(default_factory=list)
    
    # Language preference
    language: str | None = None
    
    # Workflow metadata
    workflow_id: str | None = None
    additional_input: dict[str, Any] | None = None  # For mid-flow resume with new user input
    
    def get_task_result(self, task_id: str) -> TaskResult | None:
        """Get result from a completed task."""
        return self.task_results.get(task_id)
    
    def set_task_result(self, task_id: str, result: TaskResult) -> None:
        """Store task result."""
        self.task_results[task_id] = result
    
    def get_result_data(self, task_id: str, key: str, default: Any = None) -> Any:
        """Get specific data from a task result (for conditions/dependencies)."""
        result = self.task_results.get(task_id)
        if result and result.data:
            return result.data.get(key, default)
        return default
