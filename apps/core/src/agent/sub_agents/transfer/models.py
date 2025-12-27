"""Pydantic models for transfer entity extraction."""

from pydantic import BaseModel, Field, field_validator


class MoneyAmount(BaseModel):
    value: float | None = Field(description="Numeric amount if explicitly mentioned")
    currency: str | None = Field(description="ISO currency code if present (e.g., NGN)")
    raw_text: str | None = Field(description="Original amount text as given by the user")


class SimpleTransferEntities(BaseModel):
    """Simplified entities for simple transfer flow (no complex features)."""

    recipient_name: str | None = Field(
        default=None, description="Recipient person or business name if provided"
    )
    recipient_account: str | None = Field(
        default=None, description="Recipient account number if provided"
    )
    bank_code: str | None = Field(default=None, description="Recipient bank code if provided")
    bank_name: str | None = Field(default=None, description="Recipient bank name if provided")
    source_account_id: str | None = Field(
        default=None, description="Explicit source account id/reference if provided"
    )
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


class TransferEntities(BaseModel):
    """Full entities model for complex transfers (includes pooling, dynamic amounts, etc)."""

    recipient_name: str | None = Field(description="Recipient person or business name if provided")
    recipient_account: str | None = Field(description="Recipient account number if provided")
    bank_code: str | None = Field(description="Recipient bank code if provided")
    bank_name: str | None = Field(description="Recipient bank name if provided")

    source_account_id: str | None = Field(
        description="Explicit source account id/reference if provided"
    )
    prefer_accounts: list[str] | None = Field(
        default_factory=list, description="User preference like 'use savings first' in order"
    )

    amount: MoneyAmount = Field(default_factory=MoneyAmount)
    dynamic_amount_expression: str | None = Field(
        description="Expressions like '10% of salary account' if present"
    )
    allow_pooling: bool | None = Field(description="If user allows pooling funds across accounts")
    notes: str | None = Field(description="Any extra constraints or notes relevant to execution")

    @field_validator("prefer_accounts", mode="before")
    @classmethod
    def _normalize_prefer_accounts(cls, v):
        if v is None:
            return []
        return v
