"""Pydantic models for transfer entity extraction."""

from pydantic import BaseModel, Field, field_validator


class TransferEntities(BaseModel):
    """Entities for transfer flow with optional dual-account pooling."""

    recipient_name: str | None = Field(default=None, description="Recipient person or business name if provided")
    recipient_account: str | None = Field(default=None, description="Recipient account number if provided")
    bank_code: str | None = Field(default=None, description="Recipient bank code if provided")
    bank_name: str | None = Field(default=None, description="Recipient bank name if provided")
    source_account_id: str | None = Field(default=None, description="Explicit source account id/reference if provided")
    source_bank_name: str | None = Field(
        default=None,
        description="Source bank name for internal transfers (e.g., 'Access Bank', 'GTBank')",
    )
    amount: float | None = Field(default=None, description="Transfer amount as numeric value")
    narration: str | None = Field(default=None, description="Transfer description/memo (optional)")
    transfer_all: bool | None = Field(
        default=None,
        description="True if user wants to transfer entire balance ('move all', 'everything', 'empty')",
    )
    transfer_percentage: float | None = Field(
        default=None,
        description="Percentage of balance to transfer (e.g., 50 for 'half', 25 for 'quarter', 33.33 for 'one third')",
    )
    reference_transaction: str | None = Field(
        default=None,
        description=(
            "Reference to previous transaction ('last time', 'yesterday', 'to mum last week', 'my last transfer')"
        ),
    )
    amount_multiplier: float | None = Field(
        default=None,
        description="Multiplier for referenced amount (2.0 for 'double', 0.5 for 'half of', 1.0 for 'same')",
    )

    source_accounts: list[str] | None = Field(
        default=None,
        description="Up to 2 source banks for pooling (e.g., ['Access Bank', 'GTBank'])",
    )
    use_dual_accounts: bool | None = Field(
        default=None,
        description="True if user wants to use both accounts ('use my 2 accounts', 'from both')",
    )
    explicit_split: dict[str, float] | None = Field(
        default=None,
        description="User-specified split amounts (e.g., {'Access Bank': 60000, 'GTBank': 40000})",
    )

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, v):
        """Convert amount to float, let validation node handle limit checks."""
        if v is None:
            return v
        try:
            amount = float(v)
            return amount
        except (ValueError, TypeError):
            return None

    @field_validator("transfer_percentage", mode="before")
    @classmethod
    def validate_transfer_percentage(cls, v):
        """Validate that percentage is within bounds when provided."""
        if v is None:
            return v
        try:
            pct = float(v)
            # Reject clearly invalid percentages
            if pct <= 0 or pct > 100:
                return None  # Treat invalid percentages as not provided
            return pct
        except (ValueError, TypeError):
            return None

    @field_validator("amount_multiplier", mode="before")
    @classmethod
    def validate_amount_multiplier(cls, v):
        """Validate that multiplier is positive when provided."""
        if v is None:
            return v
        try:
            mult = float(v)
            if mult <= 0:
                return None
            return mult
        except (ValueError, TypeError):
            return None
