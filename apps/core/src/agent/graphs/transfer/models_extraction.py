"""Extraction result models for transfer parsing."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from apps.core.src.agent.graphs.transfer.models import TransferEntities


class Correction(BaseModel):
    """Explicit correction detected from user input."""

    field: str = Field(description="Field being corrected: amount, recipient_account, bank_name, etc.")
    old_value: Any | None = Field(default=None, description="Previous value (if known)")
    new_value: Any = Field(description="New corrected value")


class TransferExtractionResult(BaseModel):
    """Result for the transfer extraction task."""

    intent: Literal["transfer"] = Field(default="transfer")
    entities: TransferEntities | None = Field(default=None)
    missing_fields: list[str] = Field(default_factory=list, alias="missingFields")
    reply: str = Field(default="")

    # Explicit correction detection
    correction: Correction | None = Field(
        default=None,
        description="Correction detected when user updates a previously provided value",
    )

    # Ambiguity detection for proactive clarification
    ambiguities: list[str] = Field(
        default_factory=list,
        description=("Detected ambiguities: MULTIPLE_BENEFICIARIES, UNCLEAR_BANK, AMOUNT_UNCLEAR, UNCLEAR_RECIPIENT"),
    )
