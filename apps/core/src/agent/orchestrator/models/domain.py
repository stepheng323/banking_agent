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


class MetaIntent(str, Enum):
    """Deterministic intents for meta interactions."""

    IDENTITY = "identity"
    CAPABILITIES = "capabilities"
    LIMITS = "limits"
    BRAND_ORIGIN = "brand_origin"
    GREETING = "greeting"
    THANKS = "thanks"



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
    type: Literal["transfer", "query", "airtime", "data", "account", "support", "faq", "beneficiary", "orchestrator"]
    depends_on: list[str] = Field(default_factory=list)
    stage: TaskStage = TaskStage.DRAFT
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


class TransactionOutcome(str, Enum):
    """Standardized outcome for a transaction worker pass."""

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


class TransactionResult(BaseModel):
    """Result returned by Transaction (Transfer/Airtime/Data) Worker Graphs.

    This is ephemeral. The orchestrator uses it to update the TaskSpec.
    """

    outcome: TransactionOutcome

    patch: dict[str, Any] = Field(default_factory=dict)
    response: str | None = None

    required_fields: list[str] = Field(default_factory=list)
    prompt: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    confirmation_snapshot: dict[str, Any] | None = None
    confirmation_summary: str | None = None
    update_message: str | None = None

    receipt: dict[str, Any] | None = None

    error: str | None = None
    retryable: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.outcome == TransactionOutcome.FAILED


class AccountOutcome(str, Enum):
    """Standardized outcomes for account worker."""

    OK = "ok"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"


class AccountResult(BaseModel):
    """Result returned by AccountWorker."""

    outcome: AccountOutcome
    patch: dict[str, Any] = Field(default_factory=dict)
    required_fields: list[str] = Field(default_factory=list)
    prompt: str | None = None
    response: str | None = None
    outbox: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class FAQOutcome(str, Enum):
    """Outcomes for FAQ worker."""

    OK = "ok"
    FAILED = "failed"


class FAQResult(BaseModel):
    """Result returned by FAQWorker."""

    outcome: FAQOutcome
    response: str | None = None
    should_route_to_support: bool = False
    error: str | None = None


class SupportOutcome(str, Enum):
    """Outcomes for Support worker."""

    OK = "ok"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"


class SupportResult(BaseModel):
    """Result returned by SupportWorker."""

    outcome: SupportOutcome
    response: str | None = None
    escalation: Any | None = None
    ticket_code: str | None = None
    final_message: str | None = None
    error: str | None = None


class ActiveSession(BaseModel):
    """Represents an active interactive session on the stack."""

    domain: str  # "query", "transfer", "support", etc.
    state: Literal["WAITING_FOR_INPUT", "WAITING_FOR_AUTH", "RUNNING"]
    interrupt_policy: Literal["BLOCK", "CONFIRM", "ALLOW"]
    resume_hint: dict[str, Any] = Field(default_factory=dict)
    ttl_expires_at: float | None = None
