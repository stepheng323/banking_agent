from enum import Enum


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
