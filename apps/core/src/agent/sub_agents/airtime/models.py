"""Pydantic model for airtime entity extraction results."""

from typing import Optional
from pydantic import BaseModel, Field


class SimpleAirtimeEntities(BaseModel):
    """Simplified entities for airtime purchase flow."""

    amount: Optional[float] = Field(
        default=None,
        description="Airtime purchase amount as numeric value (e.g., 2000.0 for '2k')"
    )
    recipient_phone: Optional[str] = Field(
        default=None,
        description="Recipient phone number in normalized 10-digit format (e.g., '08012345678')"
    )
    network: Optional[str] = Field(
        default=None,
        description="Mobile network name: 'MTN', 'Airtel', 'Glo', or '9mobile'"
    )
    recipient_name: Optional[str] = Field(
        default=None,
        description="Recipient name/alias when mentioned (e.g., 'mum', 'John', 'my line')"
    )
    narration: Optional[str] = Field(
        default=None,
        description="Purchase description/memo if provided (optional)"
    )
    source_account_id: Optional[str] = Field(
        default=None,
        description="Explicit source account id/reference if user specifies which account to use"
    )


class AirtimeExtractionResult(BaseModel):
    """Result of airtime entity extraction."""

    entities: SimpleAirtimeEntities = Field(
        default_factory=SimpleAirtimeEntities,
        description="Extracted airtime purchase entities"
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description="List of missing required fields: 'amount', 'recipientPhone', 'network'"
    )
    reply: str = Field(
        description="Natural language reply acknowledging extraction and asking for missing fields"
    )
