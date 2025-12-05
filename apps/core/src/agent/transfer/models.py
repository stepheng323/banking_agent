"""Pydantic models for transfer entity extraction."""

from typing import Optional, List
from pydantic import BaseModel, Field, field_validator


class MoneyAmount(BaseModel):
    value: Optional[float] = Field(description="Numeric amount if explicitly mentioned")
    currency: Optional[str] = Field(description="ISO currency code if present (e.g., NGN)")
    raw_text: Optional[str] = Field(description="Original amount text as given by the user")


class SimpleTransferEntities(BaseModel):
    """Simplified entities for simple transfer flow (no complex features)."""

    recipient_name: Optional[str] = Field(default=None, description="Recipient person or business name if provided")
    recipient_account: Optional[str] = Field(default=None, description="Recipient account number if provided")
    bank_code: Optional[str] = Field(default=None, description="Recipient bank code if provided")
    bank_name: Optional[str] = Field(default=None, description="Recipient bank name if provided")
    source_account_id: Optional[str] = Field(default=None, description="Explicit source account id/reference if provided")
    amount: Optional[float] = Field(default=None, description="Transfer amount as numeric value")
    narration: Optional[str] = Field(default=None, description="Transfer description/memo (optional)")


class TransferEntities(BaseModel):
    """Full entities model for complex transfers (includes pooling, dynamic amounts, etc)."""

    recipient_name: Optional[str] = Field(description="Recipient person or business name if provided")
    recipient_account: Optional[str] = Field(description="Recipient account number if provided")
    bank_code: Optional[str] = Field(description="Recipient bank code if provided")
    bank_name: Optional[str] = Field(description="Recipient bank name if provided")

    source_account_id: Optional[str] = Field(description="Explicit source account id/reference if provided")
    prefer_accounts: Optional[List[str]] = Field(default_factory=list, description="User preference like 'use savings first' in order")

    amount: MoneyAmount = Field(default_factory=MoneyAmount)
    dynamic_amount_expression: Optional[str] = Field(description="Expressions like '10% of salary account' if present")
    allow_pooling: Optional[bool] = Field(description="If user allows pooling funds across accounts")
    notes: Optional[str] = Field(description="Any extra constraints or notes relevant to execution")

    @field_validator("prefer_accounts", mode="before")
    @classmethod
    def _normalize_prefer_accounts(cls, v):
        if v is None:
            return []
        return v


