"""Support models for v2 micro-resolver architecture."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


SCHEMA_VERSION = 1


class SupportIntent(str, Enum):
    """Classified support intent types."""
    
    # New v2 intents
    FAILED_TRANSFER = "failed_transfer"
    PENDING_TRANSFER = "pending_transfer"
    REVERSAL_REFUND = "reversal_refund"
    WRONG_RECIPIENT = "wrong_recipient"
    FRAUD_REPORT = "fraud_report"
    ACCOUNT_LINKING = "account_linking"
    LIMITS_FEES = "limits_fees"
    RECEIPT_REQUEST = "receipt_request"
    HUMAN_HANDOFF = "human_handoff"
    GENERAL_TX_ISSUE = "general_tx_issue"
    TICKET_STATUS = "ticket_status"  # "any update?", "what's happening with my case?"
    
    # Legacy intents for backward compatibility
    TRANSFER_STATUS = "transfer_status"
    TRANSFER_FAILURE_REASON = "transfer_failure_reason"
    REVERSAL_REFUND_STATUS = "reversal_refund_status"
    RETRY_TRANSFER = "retry_transfer"
    WRONG_DEBIT = "wrong_debit"
    FRAUD_SUSPECTED = "fraud_suspected"
    SUPPORT_ESCALATION = "support_escalation"


class RequestedAction(str, Enum):
    """Actions the user is requesting (LLM detects)."""
    
    LOOKUP_TRANSACTION = "LOOKUP_TRANSACTION"
    EXPLAIN_STATUS = "EXPLAIN_STATUS"
    RETRY_PAYOUT = "RETRY_PAYOUT"
    INITIATE_REFUND = "INITIATE_REFUND"
    CREATE_TICKET = "CREATE_TICKET"
    ESCALATE = "ESCALATE"


class TransactionReference(BaseModel):
    """Reference to identify a transaction."""
    
    transaction_id: str | None = Field(default=None, description="Explicit transaction ID")
    amount: float | None = Field(default=None, description="Transaction amount")
    recipient_name: str | None = Field(default=None, description="Who was it sent to")
    date_hint: str | None = Field(default=None, description="'today', 'yesterday', or date")
    use_quoted: bool = Field(default=False, description="Use quoted message for context")
    use_recent: bool = Field(default=False, description="Use most recent tx")


class SupportContext(BaseModel):
    """Session context for support continuity."""
    
    last_transaction_ref: str | None = Field(default=None, description="Last resolved tx ID")
    last_ticket_id: str | None = Field(default=None, description="Last created ticket code")
    last_issue_intent: SupportIntent | None = Field(default=None)
    last_support_step: str | None = Field(default=None, description="collect_ref, explained_status, etc")
    attempts: int = Field(default=0, description="Resolution attempts in this session")


class SupportExtractionResult(BaseModel):
    """v2: Pure extraction result for support classification."""

    schema_version: int = Field(default=SCHEMA_VERSION)
    intent: SupportIntent = Field(default=SupportIntent.GENERAL_TX_ISSUE)
    intent_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    
    transaction_ref: TransactionReference = Field(
        default_factory=TransactionReference,
        description="How user referenced the transaction",
    )
    
    requested_actions: list[RequestedAction] = Field(
        default_factory=list,
        description="Actions user wants to take (LLM detects)",
    )
    
    raw_issue: str | None = Field(default=None, description="User's description of issue")


class EscalationResult(BaseModel):
    """Result indicating human handoff is needed."""

    action: Literal["handoff_to_human"] = "handoff_to_human"
    reason: str = ""  # "pending_over_sla", "fraud_suspected", "max_attempts"
    transaction_id: str | None = None
    context: dict = Field(default_factory=dict)


class SupportResponse(BaseModel):
    """Response from support handler."""

    message: str
    escalation: EscalationResult | None = None
    next_step: str | None = Field(default=None, description="What resolver suggests next")
    offer_receipt: bool = False
    offer_retry: bool = False
    transaction_data: dict | None = None


# Legacy ClassificationResult for existing classifier compatibility
from dataclasses import dataclass, field as dataclass_field

@dataclass
class ClassificationResult:
    """Result of support intent classification (legacy, used by classifier.py)."""
    
    intent: SupportIntent | None
    confidence: float
    transaction_ref: TransactionReference | None = None
    raw_message: str = ""
