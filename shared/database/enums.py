"""Database enums for all models."""

from enum import Enum


class UserOnboardingStatusEnum(str, Enum):
    """User onboarding status enum."""

    ONBOARDING_STARTED = "onboarding_started"
    ONBOARDING_COMPLETED = "onboarding_completed"


class MandateStatusEnum(str, Enum):
    """Account mandate status enum."""

    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class TransactionTypeEnum(str, Enum):
    """Transaction type enum."""

    TRANSFER = "transfer"
    AIRTIME = "airtime"
    DATA = "data"
    BILL = "bill"


class TransactionStatusEnum(str, Enum):
    """Transaction status enum."""

    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESSFUL = "successful"
    FAILED = "failed"
    REVERSED = "reversed"


class FundedTransferStatusEnum(str, Enum):
    """Status enum for multi-account funded transfers."""

    DRAFT = "draft"
    FUNDING_PENDING = "funding_pending"
    FUNDING_COMPLETE = "funding_complete"
    PAYOUT_PENDING = "payout_pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDING = "refunding"
    REFUNDED = "refunded"


class FundingStepStatusEnum(str, Enum):
    """Status enum for individual funding steps (debits)."""

    PENDING = "pending"
    PROCESSING = "processing"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    REFUND_PENDING = "refund_pending"
    REFUNDED = "refunded"


class BeneficiaryTypeEnum(str, Enum):
    """Beneficiary type enum."""

    TRANSFER = "transfer"
    AIRTIME = "airtime"
    DATA = "data"


class ActionableMessageTypeEnum(str, Enum):
    """Type of actionable message stored for quote-based interactions."""

    TRANSFER_RECEIPT = "transfer_receipt"
    AIRTIME_RECEIPT = "airtime_receipt"
    DATA_RECEIPT = "data_receipt"
    CONFIRMATION_REQUEST = "confirmation_request"


class SupportTicketStatusEnum(str, Enum):
    """Support ticket status enum."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class SupportTicketPriorityEnum(str, Enum):
    """Support ticket priority enum."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class SupportChannelEnum(str, Enum):
    """Channel through which support was requested."""

    WHATSAPP = "whatsapp"
    WEB = "web"
    APP = "app"
