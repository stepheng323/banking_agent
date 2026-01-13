"""Extraction result models for transfer parsing."""

from typing import Literal

from pydantic import BaseModel, Field

from apps.core.src.agent.graphs.transfer.models import TransferEntities

CorrectionField = Literal[
    "amount",
    "recipient_account",
    "recipient_name",
    "bank_name",
    "narration",
    "transfer_percentage",
    "source_bank_name",
]
CorrectionValue = str | float | int | None


class Correction(BaseModel):
    """Explicit correction detected from user input."""

    field: CorrectionField = Field(description="Field being corrected")
    old_value: CorrectionValue = Field(default=None, description="Previous value (if known)")
    new_value: str | float | int = Field(description="New corrected value")


class TransferExtractionResult(BaseModel):
    """Result for the transfer extraction task."""

    intent: Literal["transfer"] = Field(default="transfer")
    entities: TransferEntities | None = Field(default=None)
    missing_fields: list[str] = Field(default_factory=list, alias="missingFields")
    reply: str = Field(default="")

    correction: Correction | None = Field(
        default=None,
        description="Correction detected when user updates a previously provided value",
    )

    ambiguities: list[str] = Field(
        default_factory=list,
        description=("Detected ambiguities: MULTIPLE_BENEFICIARIES, UNCLEAR_BANK, AMOUNT_UNCLEAR, UNCLEAR_RECIPIENT"),
    )
