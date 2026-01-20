"""Canonical Domain Models for Banking Agent V3 Architecture.

These models define the contract between the Orchestrator (State Owner)
and the Domain Workers (Stateless Logic).
"""

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

# --- 1. Task Lifecycle ---


class TaskStage(str, Enum):
    """Lifecycle stages for a task."""

    DRAFT = "draft"
    EXTRACTED = "extracted"
    RESOLVED = "resolved"
    VALIDATED = "validated"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_AUTH = "awaiting_auth"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# --- 2. Task Definition (Durable Truth) ---


class TransferConfirmation(BaseModel):
    """Confirmation state within a transfer payload."""

    token: str | None = None
    summary: str | None = None
    snapshot_hash: str | None = None
    confirmed: bool = False


class TransferPayload(BaseModel):
    """Payload for a Transfer task."""

    amount: float | None = None
    recipient_name: str | None = None
    recipient_resolved_name: str | None = None  # Official name from bank
    recipient_account: str | None = None
    recipient_bank_name: str | None = None
    recipient_bank_code: str | None = None
    beneficiary_id: str | None = None

    source_account_id: str | None = None
    source_bank_name: str | None = None
    source_account_number: str | None = None

    narration: str | None = None

    # Special modes
    transfer_all: bool = False
    transfer_percentage: float | None = None

    # Execution Logic
    idempotency_key: str | None = None
    funding_plan: dict[str, Any] | None = None

    # Confirmation sub-state
    confirmation: TransferConfirmation = Field(default_factory=TransferConfirmation)


class TaskSpec(BaseModel):
    """Generic task container.

    The Orchestrator persists this. Subgraphs receive the payload
    and return patches to it.
    """

    id: str
    type: Literal["transfer", "query", "airtime", "data"]
    stage: TaskStage = TaskStage.DRAFT

    # durable task truth used by subgraphs
    # We keep this generic logic-wise, but for Transfers it will follow TransferPayload schema
    payload: dict[str, Any] = Field(default_factory=dict)


# --- 3. Interrupts (One distinct blocker at a time) ---


class PendingInterrupt(BaseModel):
    """A blocking state that requires user intervention.

    The orchestrator holds exactly one of these if blocked.
    """

    kind: Literal["input", "confirmation", "auth"]
    task_ids: list[str]

    # input specific
    fields_by_task: dict[str, list[str]] = Field(default_factory=dict)
    prompt: str | None = None

    # confirmation specific
    confirmation_token: str | None = None

    # auth specific
    auth_method: Literal["pin", "otp"] | None = None


# --- 4. Subgraph Contracts ---


class TransferOutcome(str, Enum):
    """Standardized outcome for a transfer worker pass."""

    OK = "ok"
    NEEDS_INPUT = "needs_input"
    NEEDS_CONFIRMATION = "needs_confirmation"
    NEEDS_AUTH = "needs_auth"
    FAILED = "failed"


class WorkerOutcome(str, Enum):
    """Standardized outcome for a generic worker pass."""

    SUCCESS = "success"
    FAILED = "failed"


class WorkerResult(BaseModel):
    """Generic result returned by any Worker."""

    outcome: WorkerOutcome
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class TransferResult(BaseModel):
    """Result returned by the Transfer Worker Graph.

    This is ephemeral. The orchestrator uses it to update the TaskSpec.
    """

    outcome: TransferOutcome

    patch: dict[str, Any] = Field(default_factory=dict)

    required_fields: list[str] = Field(default_factory=list)
    prompt: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    confirmation_snapshot: dict[str, Any] | None = None
    confirmation_summary: str | None = None

    receipt: dict[str, Any] | None = None

    error: str | None = None
    retryable: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.outcome == TransferOutcome.FAILED
