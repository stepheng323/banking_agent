"""Data purchase entity extraction models v2. Pure extraction, no business logic."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

# Schema version for future-proofing
SCHEMA_VERSION = 1


class DataPurchaseEntities(BaseModel):
    """Entities extracted for data purchase."""

    recipient_phone: str | None = Field(
        default=None,
        description="Phone number to purchase data for (11-digit format: 08012345678)",
    )
    network: str | None = Field(
        default=None,
        description="Network provider: MTN, AIRTEL, GLO, 9MOBILE",
    )
    budget: float | None = Field(
        default=None,
        description="Budget amount in Naira for data purchase (e.g., 2000 for '2k worth')",
    )
    size_preference: str | None = Field(
        default=None,
        description="Preferred data size: '1GB', '2GB', '5GB', 'weekly', 'monthly'",
    )
    is_self: bool | None = Field(
        default=None,
        description="True if user wants data for their own line",
    )
    recipient_name: str | None = Field(
        default=None,
        description="Recipient name/alias if mentioned ('for mum', 'for brother')",
    )


class CorrectionField(str, Enum):
    """Fields that can be corrected."""

    RECIPIENT_PHONE = "recipient_phone"
    NETWORK = "network"
    BUDGET = "budget"
    SIZE_PREFERENCE = "size_preference"


class AmbiguityCode(str, Enum):
    """Structured ambiguity codes."""

    BUDGET_UNCLEAR = "BUDGET_UNCLEAR"
    NETWORK_UNCLEAR = "NETWORK_UNCLEAR"
    SIZE_UNCLEAR = "SIZE_UNCLEAR"
    RECIPIENT_UNCLEAR = "RECIPIENT_UNCLEAR"


class RequestedFeature(str, Enum):
    """Features beyond simple data purchase."""

    SCHEDULED = "SCHEDULED"
    RECURRING = "RECURRING"


class DataCorrection(BaseModel):
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


class DataExtractionResult(BaseModel):
    """v2: Pure extraction result with versioned envelope."""

    schema_version: int = Field(default=SCHEMA_VERSION, description="Schema version for future-proofing")
    intent: Literal["data"] = Field(default="data")
    intent_confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence in intent")

    entities: DataPurchaseEntities = Field(
        default_factory=DataPurchaseEntities,
        description="Extracted data purchase entities",
    )

    correction: DataCorrection | None = Field(
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
