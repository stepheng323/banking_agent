"""Data purchase entity extraction models."""

from typing import Literal

from pydantic import BaseModel, Field


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


DataCorrectionField = Literal["recipient_phone", "network", "budget", "size_preference"]


class DataCorrection(BaseModel):
    """Explicit correction detected from user input."""

    field: DataCorrectionField = Field(description="Field being corrected")
    old_value: str | float | None = Field(default=None, description="Previous value (if known)")
    new_value: str | float = Field(description="New corrected value")


class DataExtractionResult(BaseModel):
    """Result of data purchase entity extraction."""

    entities: DataPurchaseEntities = Field(
        default_factory=DataPurchaseEntities,
        description="Extracted data purchase entities",
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        alias="missingFields",
        description="Missing required fields: 'recipientPhone', 'network'",
    )
    reply: str = Field(
        default="",
        description="Natural language reply to user",
    )
    correction: DataCorrection | None = Field(
        default=None,
        description="Correction detected when user updates a value",
    )
    ambiguities: list[str] = Field(
        default_factory=list,
        description="Detected ambiguities: BUDGET_UNCLEAR, NETWORK_UNCLEAR, SIZE_UNCLEAR",
    )
