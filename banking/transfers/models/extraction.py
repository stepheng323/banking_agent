"""Extraction result models for transfer parsing.

Clean extraction architecture with versioned envelope.
- LLM outputs pure extraction, no business logic
- Resolver computes missing fields and decision
- Formatter generates response
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from banking.transfers.models.entities import TransferEntities
from shared.types.amount_mutation import AmountMutation

SCHEMA_VERSION = 1


class CorrectionField(str, Enum):
    """Fields that can be corrected."""

    AMOUNT = "amount"
    RECIPIENT_ACCOUNT = "recipient_account"
    RECIPIENT_NAME = "recipient_name"
    BANK_NAME = "bank_name"
    NARRATION = "narration"
    TRANSFER_PERCENTAGE = "transfer_percentage"
    SOURCE_BANK_NAME = "source_bank_name"


class AmbiguityCode(str, Enum):
    """Structured ambiguity codes."""

    AMOUNT_UNCLEAR = "AMOUNT_UNCLEAR"
    MULTIPLE_BENEFICIARIES = "MULTIPLE_BENEFICIARIES"
    UNCLEAR_BANK = "UNCLEAR_BANK"
    UNCLEAR_RECIPIENT = "UNCLEAR_RECIPIENT"


class RequestedFeature(str, Enum):
    """Features beyond simple transfer."""

    SCHEDULED = "SCHEDULED"
    RECURRING = "RECURRING"
    INTERNATIONAL = "INTERNATIONAL"


class Correction(BaseModel):
    """Explicit correction detected from user input."""

    field: CorrectionField | None = Field(default=None, description="Field being corrected")
    new_value: str | float | int | None = Field(default=None, description="New corrected value")
    amount_mutation: AmountMutation | None = Field(
        default=None,
        description="For amount corrections, the authoritative bounded mutation of the pending amount",
    )


class Ambiguity(BaseModel):
    """Structured ambiguity with candidates."""

    code: AmbiguityCode = Field(description="Ambiguity type")
    candidates: list[str | float] = Field(default_factory=list, description="Possible values")


class References(BaseModel):
    """References to context (e.g., recent transfers)."""

    use_recent_transfer: bool = Field(default=False, description="User wants to use recent transfer")
    recent_transfer_index: int | None = Field(default=None, description="Index if explicit")


class TransferExtractionResult(BaseModel):
    """Pure extraction result."""

    schema_version: int = Field(default=SCHEMA_VERSION, description="Schema version for future-proofing")
    intent: Literal["transfer"] = Field(default="transfer")
    intent_confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence in intent")

    entities: TransferEntities | None = Field(default=None)

    correction: Correction | None = Field(default=None, description="Correction if user updated a value")

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
        description="Features beyond simple transfer: SCHEDULED, RECURRING, INTERNATIONAL",
    )

    acknowledgment: str | None = Field(
        default=None,
        description="Natural language acknowledgment of update/correction (e.g. 'Got it, added the narration')",
    )

    confirmation_intent: Literal["confirm", "cancel", "update"] | None = Field(
        default=None,
        description="Intent detected during confirmation phase",
    )
