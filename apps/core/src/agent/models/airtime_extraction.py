"""Pydantic model for airtime entity extraction results."""

from typing import Optional
from pydantic import BaseModel, Field


class AirtimeExtractionResult(BaseModel):
    """Result of airtime entity extraction."""

    entities: dict = Field(
        description="Extracted entities: amount (float), recipient_phone (str), network (str: MTN, Airtel, Glo, 9mobile)"
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description="List of missing required fields: 'amount', 'recipientPhone', 'network'"
    )
    reply: str = Field(
        description="Natural language reply acknowledging extraction and asking for missing fields"
    )

