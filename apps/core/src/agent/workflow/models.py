"""Workflow engine models.

Core types for DAG-based workflow execution.
"""

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class TaskRisk(str, Enum):
    """Risk classification for authorization gating."""
    
    READ_ONLY = "read_only"       # queries, balance, receipts
    MUTATION = "mutation"         # save beneficiary, rename
    MONEY_MOVE = "money_move"     # transfer, airtime, data


class InterruptPolicy(str, Enum):
    """Policy for handling new requests during active workflows."""
    
    ALLOW = "allow"          # Queries/Support allowed (pause current)
    BLOCK = "block"          # Reject new request (finish current first)
    CONFIRM = "confirm"      # Ask user if they want to switch


class TaskStatus(str, Enum):
    """Execution status of a task."""
    
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    AWAITING_AUTH = "awaiting_auth"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskResult(BaseModel):
    """Result from a task handler execution."""
    
    task_id: str
    status: TaskStatus
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    user_prompt: str | None = None  # Message to show user (for NEEDS_INPUT)


class WorkflowStatus(str, Enum):
    """Overall workflow execution status."""
    
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    AWAITING_AUTH = "awaiting_auth"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL_SUCCESS = "partial_success"


class WorkflowResult(BaseModel):
    """Result from workflow DAG execution."""
    
    status: WorkflowStatus
    task_results: dict[str, TaskResult] = Field(default_factory=dict)
    missing_by_task: dict[str, list[str]] = Field(default_factory=dict)
    pending_auth_tasks: list[str] = Field(default_factory=list)
    user_message: str | None = None
    workflow_id: str | None = None
    
    @classmethod
    def awaiting_auth(cls, task_ids: list[str]) -> "WorkflowResult":
        return cls(
            status=WorkflowStatus.AWAITING_AUTH,
            pending_auth_tasks=task_ids,
        )
    
    @classmethod
    def waiting_for_input(cls, missing_by_task: dict[str, list[str]], message: str) -> "WorkflowResult":
        return cls(
            status=WorkflowStatus.WAITING_FOR_INPUT,
            missing_by_task=missing_by_task,
            user_message=message,
        )
    
    @classmethod
    def complete(cls, task_results: dict[str, TaskResult]) -> "WorkflowResult":
        failed = [k for k, v in task_results.items() if v.status == TaskStatus.FAILED]
        if failed:
            return cls(status=WorkflowStatus.PARTIAL_SUCCESS, task_results=task_results)
        return cls(status=WorkflowStatus.COMPLETED, task_results=task_results)


# Risk mapping by executor
EXECUTOR_RISK_MAP: dict[str, TaskRisk] = {
    "query": TaskRisk.READ_ONLY,
    "receipt": TaskRisk.READ_ONLY,
    "support": TaskRisk.READ_ONLY,
    "manage_accounts": TaskRisk.MUTATION,
    "transfer": TaskRisk.MONEY_MOVE,
    "airtime": TaskRisk.MONEY_MOVE,
    "data": TaskRisk.MONEY_MOVE,
    "utility": TaskRisk.MONEY_MOVE,
}


def get_task_risk(executor: str) -> TaskRisk:
    """Get risk classification for an executor."""
    return EXECUTOR_RISK_MAP.get(executor, TaskRisk.READ_ONLY)


def requires_authorization(executor: str) -> bool:
    """Check if executor requires PIN authorization."""
    return get_task_risk(executor) == TaskRisk.MONEY_MOVE
