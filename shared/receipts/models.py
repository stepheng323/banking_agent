"""Transfer receipt data models."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class DebitSource(BaseModel):
    """A single debit source in a pooled transfer."""

    account_number: str = Field(description="Source account number (will be masked in receipt)")
    bank_name: str = Field(description="Source bank name")
    amount: Decimal = Field(description="Amount debited from this account")


class TransferReceiptData(BaseModel):
    """Data required to generate a transfer receipt.

    This model is self-contained and does not depend on conversation or WhatsApp state.
    Receipts are deterministic: the same data always produces the same receipt.
    """

    transaction_reference: str = Field(description="Unique transaction reference")
    status: Literal["success"] = Field(default="success", description="Only successful transfers get receipts")
    amount: Decimal = Field(description="Amount credited to recipient")
    total_debited: Decimal = Field(description="Total debited including fees")
    fee: Decimal = Field(description="Transaction fee")
    recipient_name: str = Field(description="Recipient's name")
    recipient_account: str = Field(description="Recipient account number (will be masked)")
    recipient_bank: str = Field(description="Recipient's bank name")
    debit_sources: list[DebitSource] = Field(
        default_factory=list, description="List of accounts debited (for pooled transfers)"
    )
    created_at: datetime = Field(default_factory=datetime.now, description="Transaction timestamp")
    narration: str | None = Field(default=None, description="Optional transfer narration/memo")
    sender_name: str | None = Field(default=None, description="Sender's name (for display)")
    sender_account: str | None = Field(default=None, description="Sender's account number")
    sender_bank: str | None = Field(default=None, description="Sender's bank name")

    @property
    def is_pooled(self) -> bool:
        """Check if this is a pooled transfer (multiple debit sources)."""
        return len(self.debit_sources) > 1
