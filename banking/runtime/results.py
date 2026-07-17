"""Shared domain worker result contracts."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from shared.queue.models import ReceiptJobPayload
from shared.types.read import ReadResult


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
    """Generic result returned by any worker."""

    outcome: WorkerOutcome
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class TransactionResult(BaseModel):
    """Result returned by transaction worker graphs."""

    outcome: TransactionOutcome

    patch: dict[str, Any] = Field(default_factory=dict)
    response: str | None = None

    required_fields: list[str] = Field(default_factory=list)
    prompt: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    read_result: ReadResult | None = None

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
    NEEDS_CONFIRMATION = "needs_confirmation"
    FAILED = "failed"


class AccountResult(BaseModel):
    """Result returned by account workers."""

    outcome: AccountOutcome
    patch: dict[str, Any] = Field(default_factory=dict)
    required_fields: list[str] = Field(default_factory=list)
    prompt: str | None = None
    response: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    read_result: ReadResult | None = None
    confirmation_snapshot: dict[str, Any] | None = None
    confirmation_summary: str | None = None
    update_message: str | None = None
    outbox: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class FAQOutcome(str, Enum):
    """Outcomes for FAQ worker."""

    OK = "ok"
    FAILED = "failed"


class FAQResult(BaseModel):
    """Result returned by FAQ workers."""

    outcome: FAQOutcome
    response: str | None = None
    should_route_to_support: bool = False
    error: str | None = None


class SupportOutcome(str, Enum):
    """Outcomes for support worker."""

    OK = "ok"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"


class SupportResult(BaseModel):
    """Result returned by support workers."""

    outcome: SupportOutcome
    response: str | None = None
    receipt_jobs: list[ReceiptJobPayload] | list[dict[str, object]] = Field(default_factory=list)
    handoff: dict[str, Any] | None = None
    escalation: Any | None = None
    ticket_code: str | None = None
    final_message: str | None = None
    read_result: ReadResult | None = None
    error: str | None = None
