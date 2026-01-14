"""Pydantic model for airtime entity extraction results."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SimpleAirtimeEntities(BaseModel):
    """Simplified entities for airtime purchase flow."""

    amount: float | None = Field(
        default=None, description="Airtime purchase amount as numeric value (e.g., 2000.0 for '2k')"
    )
    recipient_phone: str | None = Field(
        default=None,
        description="Recipient phone number in normalized 10-digit format (e.g., '08012345678')",
    )
    network: str | None = Field(default=None, description="Mobile network name: 'MTN', 'Airtel', 'Glo', or '9mobile'")
    recipient_name: str | None = Field(
        default=None,
        description="Recipient name/alias when mentioned (e.g., 'mum', 'John', 'my line')",
    )
    narration: str | None = Field(default=None, description="Purchase description/memo if provided (optional)")
    source_account_id: str | None = Field(
        default=None,
        description="Explicit source account id/reference if user specifies which account to use",
    )
    is_self: bool | None = Field(
        default=None,
        description=(
            "True if user wants to recharge their own line ('to me', 'my line', 'myself', 'my number', 'for me')"
        ),
    )

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, v):
        """Validate that amount is not negative when provided."""
        if v is None:
            return v
        try:
            amount = float(v)
            if amount < 0:
                return None
            return amount
        except (ValueError, TypeError):
            return None


AirtimeCorrectionField = Literal["amount", "recipient_phone", "network", "recipient_name"]


class AirtimeCorrection(BaseModel):
    """Explicit correction detected from user input."""

    field: AirtimeCorrectionField = Field(description="Field being corrected")
    old_value: str | float | None = Field(default=None, description="Previous value (if known)")
    new_value: str | float = Field(description="New corrected value")


class AirtimeExtractionResult(BaseModel):
    """Result of airtime entity extraction."""

    entities: SimpleAirtimeEntities = Field(
        default_factory=SimpleAirtimeEntities, description="Extracted airtime purchase entities"
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description="List of missing required fields: 'amount', 'recipientPhone', 'network'",
    )
    reply: str = Field(description="Natural language reply acknowledging extraction and asking for missing fields")

    correction: AirtimeCorrection | None = Field(
        default=None,
        description="Correction detected when user updates a previously provided value",
    )
    ambiguities: list[str] = Field(
        default_factory=list,
        description="Detected ambiguities: AMOUNT_UNCLEAR, NETWORK_UNCLEAR, RECIPIENT_UNCLEAR",
    )
