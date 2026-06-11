"""Canonical Domain Models for Banking Agent V3 Architecture.

These models define the contract between the Orchestrator (State Owner)
and the Domain Workers (Stateless Logic).
"""

from enum import Enum
from time import time
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from shared.money import MoneyAmount

# --- 1. Task Lifecycle ---


class TaskStage(str, Enum):
    """Lifecycle stages for a task."""

    DRAFT = "draft"
    EXTRACTED = "extracted"
    RESOLVED = "resolved"
    VALIDATED = "validated"
    AWAITING_FUNDING_ADJUSTMENT = "awaiting_funding_adjustment"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_AUTH = "awaiting_auth"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MetaIntent(str, Enum):
    """Deterministic intents for meta interactions."""

    IDENTITY = "identity"
    CREATOR = "creator"
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

    amount: MoneyAmount | None = None
    recipient_name: str | None = None
    recipient_resolved_name: str | None = None  # Official name from bank
    recipient_account: str | None = None
    recipient_bank_name: str | None = None
    recipient_bank_code: str | None = None
    recipient_bank_code_provider: str | None = None
    recipient_resolution_provider: str | None = None
    recipient_resolution_mode: Literal["single_source", "pooled"] | None = None
    recipient_reference: dict[str, Any] | None = None
    beneficiary_id: str | None = None
    referent_recipient_candidates: list[dict[str, Any]] = Field(default_factory=list)

    source_account_id: str | None = None
    source_bank_name: str | None = None
    source_account_number: str | None = None
    use_dual_accounts: bool | None = None
    source_accounts: list[str] | None = None
    explicit_split: dict[str, MoneyAmount] | None = None

    authored_narration: str | None = None
    narration: str | None = None

    # Special modes
    transfer_all: bool = False
    transfer_percentage: float | None = None

    # Execution Logic
    idempotency_key: str | None = None
    funding_plan: dict[str, Any] | None = None
    suggested_funding_plan: dict[str, Any] | None = None

    # Scheduling (transfer phase 1)
    schedule_mode: Literal["one_time", "recurring"] | None = None
    recurrence_type: Literal["one_time", "daily", "weekly", "monthly"] | None = None
    schedule_timezone: str | None = None
    schedule_start_date: str | None = None
    schedule_time_local: str | None = None
    schedule_day_of_week: int | None = None
    schedule_day_of_month: int | None = None
    schedule_end_date: str | None = None
    schedule_id: str | None = None
    schedule_selector: str | None = None
    schedule_response_mode: Literal["list", "count"] | None = None

    # Confirmation sub-state
    confirmation: TransferConfirmation = Field(default_factory=TransferConfirmation)
    confirmation_message_scoped: bool = False
    amount_suggestion_disabled: bool = False

    @field_validator("transfer_all", mode="before")
    @classmethod
    def normalize_transfer_all(cls, value: Any) -> bool:
        if value is None:
            return False
        return bool(value)


class TaskSpec(BaseModel):
    """Generic task container.

    The Orchestrator persists this. Workers receive the payload
    and return patches to it.
    """

    id: str
    type: Literal[
        "transfer",
        "query",
        "airtime",
        "data",
        "account",
        "support",
        "faq",
        "beneficiary",
        "schedule",
        "orchestrator",
    ]
    depends_on: list[str] = Field(default_factory=list)
    stage: TaskStage = TaskStage.DRAFT
    payload: dict[str, Any] = Field(default_factory=dict)


class AuthorizationContext(BaseModel):
    """PIN authorization bound to one transaction resume event."""

    idempotency_key: str
    flow_type: str
    user_id: str | None = None
    channel: str | None = None
    verified_at_ts: float = Field(default_factory=time)
    authorized_task_idempotency_keys: list[str] = Field(default_factory=list)

    def authorized_keys(self) -> set[str]:
        keys = {self.idempotency_key}
        keys.update(key for key in self.authorized_task_idempotency_keys if key)
        return keys


# --- 3. Interrupts (One distinct blocker at a time) ---


class PendingInterrupt(BaseModel):
    """A blocking state that requires user intervention.

    The orchestrator holds exactly one of these if blocked.
    """

    kind: Literal["input", "confirmation", "auth"]
    task_ids: list[str]
    created_at_ts: float = Field(default_factory=time)
    expires_at_ts: float | None = None

    # input specific
    fields_by_task: dict[str, list[str]] = Field(default_factory=dict)
    prompt: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    # confirmation specific
    confirmation_token: str | None = None

    # auth specific
    auth_method: Literal["pin", "otp"] | None = None
    attempts: int = 0
    authorization_idempotency_key: str | None = None
    authorized_task_idempotency_keys: list[str] = Field(default_factory=list)


class ActiveSession(BaseModel):
    """Represents an active interactive session on the stack."""

    domain: str  # "query", "transfer", "support", etc.
    state: Literal["WAITING_FOR_INPUT", "WAITING_FOR_AUTH", "RUNNING"]
    interrupt_policy: Literal["BLOCK", "CONFIRM", "ALLOW"]
    resume_hint: dict[str, Any] = Field(default_factory=dict)
    ttl_expires_at: float | None = None
