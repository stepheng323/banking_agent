"""Support sub-agent models and types."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class SupportIntent(str, Enum):
    """Classified support intent types."""

    TRANSFER_STATUS = "transfer_status"
    TRANSFER_FAILURE_REASON = "transfer_failure_reason"
    PENDING_TRANSFER = "pending_transfer"
    REVERSAL_REFUND_STATUS = "reversal_refund_status"
    RETRY_TRANSFER = "retry_transfer"
    WRONG_DEBIT = "wrong_debit"
    FRAUD_SUSPECTED = "fraud_suspected"
    RECEIPT_REQUEST = "receipt_request"
    SUPPORT_ESCALATION = "support_escalation"


@dataclass
class TransactionReference:
    """Reference to identify a transaction."""

    transaction_id: str | None = None
    amount: float | None = None
    recipient_name: str | None = None
    date_hint: str | None = None  # "today", "yesterday", or date string
    quoted_message_id: str | None = None


@dataclass
class ClassificationResult:
    """Result of support intent classification."""

    intent: SupportIntent | None
    confidence: float
    transaction_ref: TransactionReference | None = None
    raw_message: str = ""


@dataclass
class EscalationResult:
    """Result indicating human handoff is needed."""

    action: Literal["handoff_to_human"] = "handoff_to_human"
    reason: str = ""  # "pending_over_sla", "fraud_suspected", "unknown_error"
    transaction_id: str | None = None
    context: dict = field(default_factory=dict)


@dataclass
class SupportResponse:
    """Response from support handler."""

    message: str
    escalation: EscalationResult | None = None
    offer_receipt: bool = False
    offer_retry: bool = False
    transaction_data: dict[str, Any] | None = None
