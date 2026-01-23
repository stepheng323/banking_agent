"""Airtime extraction models v2. Pure extraction, no business logic."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Schema version for future-proofing
SCHEMA_VERSION = 1


class SimpleAirtimeEntities(BaseModel):
    """Simplified entities for airtime purchase flow."""

    amount: float | None = Field(
        default=None, description="Airtime purchase amount as numeric value (e.g., 2000.0 for '2k')"
    )
    recipient_phone: str | None = Field(
        default=None,
        description="Recipient phone number in normalized 11-digit format (e.g., '08012345678')",
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


class CorrectionField(str, Enum):
    """Fields that can be corrected."""

    AMOUNT = "amount"
    RECIPIENT_PHONE = "recipient_phone"
    NETWORK = "network"
    RECIPIENT_NAME = "recipient_name"


class AmbiguityCode(str, Enum):
    """Structured ambiguity codes."""

    AMOUNT_UNCLEAR = "AMOUNT_UNCLEAR"
    NETWORK_UNCLEAR = "NETWORK_UNCLEAR"
    RECIPIENT_UNCLEAR = "RECIPIENT_UNCLEAR"


class RequestedFeature(str, Enum):
    """Features beyond simple airtime purchase."""

    SCHEDULED = "SCHEDULED"
    RECURRING = "RECURRING"


class AirtimeCorrection(BaseModel):
    """Explicit correction detected from user input."""

    field: CorrectionField | None = Field(default=None, description="Field being corrected")
    new_value: str | float | None = Field(default=None, description="New corrected value")


class Ambiguity(BaseModel):
    """Structured ambiguity with candidates."""

    code: AmbiguityCode = Field(description="Ambiguity type")
    candidates: list[str | float] = Field(default_factory=list, description="Possible values")


class References(BaseModel):
    """References to context (e.g., recent purchases)."""

    use_recent_purchase: bool = Field(default=False, description="User wants to repeat recent purchase")
    recent_purchase_index: int | None = Field(default=None, description="Index if explicit")


class AirtimeExtractionResult(BaseModel):
    """v2: Pure extraction result with versioned envelope."""

    schema_version: int = Field(default=SCHEMA_VERSION, description="Schema version for future-proofing")
    intent: Literal["airtime"] = Field(default="airtime")
    intent_confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence in intent")

    entities: SimpleAirtimeEntities = Field(
        default_factory=SimpleAirtimeEntities, description="Extracted airtime purchase entities"
    )

    correction: AirtimeCorrection | None = Field(
        default=None, description="Correction if user updated a value"
    )

    ambiguities: list[Ambiguity] = Field(
        default_factory=list,
        description="Structured ambiguities with candidates",
    )

    references: References = Field(
        default_factory=References,
        description="References to context",
    )

    requested_features: list[RequestedFeature] = Field(
        default_factory=list,
        description="Features beyond simple purchase: SCHEDULED, RECURRING",
    )
