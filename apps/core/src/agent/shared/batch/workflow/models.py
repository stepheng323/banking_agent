"""Workflow engine models for DAG-based task execution."""

import hashlib
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from shared.types.agent_types import TaskStatus
from shared.types.planner import PlannedTask


class ErrorKind(str, Enum):
    """Classification of errors for retry decisions."""

    TRANSIENT = "transient"  # Retry-able: network timeouts, 503s
    BUSINESS = "business"  # Non-retry-able: insufficient funds, invalid recipient
    UNKNOWN = "unknown"


class BatchErrorCategory(str, Enum):
    """Category for batch execution policy decisions."""

    CONTINUE = "continue"  # Bank-specific error, don't block other tasks
    STOP = "stop"  # User-wide issue, pause entire batch


class ProviderError(str, Enum):
    """Provider-specific error codes mapped to batch policy."""

    # Continue errors (bank-specific, don't block others)
    BANK_UNAVAILABLE = "bank_unavailable"  # → CONTINUE
    NETWORK_TIMEOUT = "network_timeout"  # → CONTINUE
    INVALID_BENEFICIARY = "invalid_beneficiary"  # → CONTINUE
    NAME_MISMATCH = "name_mismatch"  # → CONTINUE
    TEMPORARY_FAILURE = "temporary_failure"  # → CONTINUE

    # Stop errors (user-wide, pause batch)
    INSUFFICIENT_FUNDS = "insufficient_funds"  # → STOP
    AUTH_FAILED = "auth_failed"  # → STOP
    PROVIDER_DOWN = "provider_down"  # → STOP
    RATE_LIMITED = "rate_limited"  # → STOP
    RISK_BLOCKED = "risk_blocked"  # → STOP

    UNKNOWN = "unknown"  # → STOP (safer default)


# Mapping from ProviderError to BatchErrorCategory
CONTINUE_ERRORS = {
    ProviderError.BANK_UNAVAILABLE,
    ProviderError.NETWORK_TIMEOUT,
    ProviderError.INVALID_BENEFICIARY,
    ProviderError.NAME_MISMATCH,
    ProviderError.TEMPORARY_FAILURE,
}


def compute_approval_hash(tasks: list[PlannedTask]) -> str:
    """
    Compute a stable hash of the tasks to bind PIN authorization.

    Hash covers:
    - Task IDs (order matters)
    - Executor types
    - Critical parameters (amount, recipient, etc.)
    """
    # Create validatable list of task dicts
    task_data = []
    for t in tasks:
        # Extract only critical fields for approval
        data = {
            "id": t.task_id,
            "executor": t.executor,
            "params": t.parameters,
            "depends_on": sorted(t.depends_on),  # Stable order
        }
        task_data.append(data)

    # Serialize to JSON with sorted keys for stability
    payload = json.dumps(task_data, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def should_continue_batch(error: ProviderError | None) -> bool:
    """Determine if batch should continue after this error."""
    if error is None:
        return False  # Unknown error → stop
    return error in CONTINUE_ERRORS


class TaskResult(BaseModel):
    """Execution outcome for a single task."""

    task_id: str
    run_id: str = Field(..., description="Unique per execution attempt")
    status: TaskStatus
    provider_ref: str | None = None  # External reference (e.g., transaction ID)
    error_code: str | None = None
    error_message: str | None = None
    error_kind: ErrorKind | None = None
    provider_error: ProviderError | None = None  # For batch policy decisions
    data: dict[str, Any] = Field(default_factory=dict)  # Arbitrary result data

    # Debit tracking for idempotent retries
    debit_reference: str | None = None  # Set when debit API called
    transfer_reference: str | None = None  # Set when destination transfer done

    @property
    def needs_status_check(self) -> bool:
        """True if debited but not completed - check status before retry."""
        return self.debit_reference is not None and self.transfer_reference is None


class WorkflowResult(BaseModel):
    """Aggregated results from a workflow execution."""

    workflow_id: str
    completed: list[TaskResult] = Field(default_factory=list)
    failed: list[TaskResult] = Field(default_factory=list)
    skipped: list[TaskResult] = Field(default_factory=list)
    blocked: list[TaskResult] = Field(default_factory=list)
    stopped_early: bool = False  # True if batch stopped due to STOP error

    @property
    def total(self) -> int:
        """Total tasks processed."""
        return len(self.completed) + len(self.failed) + len(self.skipped) + len(self.blocked)

    @property
    def success_rate(self) -> float:
        """Percentage of tasks that completed successfully."""
        if self.total == 0:
            return 0.0
        return len(self.completed) / self.total

    @property
    def has_failures(self) -> bool:
        """Whether any tasks failed."""
        return len(self.failed) > 0

    @property
    def has_retryable_failures(self) -> bool:
        """Whether any failures are retryable."""
        return any(r.error_kind == ErrorKind.TRANSIENT for r in self.failed)

    @property
    def stop_reason(self) -> str | None:
        """Get the error that caused batch to stop."""
        if not self.stopped_early:
            return None
        for r in self.failed:
            if r.provider_error and not should_continue_batch(r.provider_error):
                return r.error_message
        return None
