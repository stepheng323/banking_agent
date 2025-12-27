"""Extraction result models for transfer parsing."""

from typing import Literal

from pydantic import BaseModel, Field

from apps.core.src.agent.sub_agents.transfer.models import SimpleTransferEntities


class TransferExtractionResult(BaseModel):
    """Result for the transfer extraction task."""

    intent: Literal["transfer"] = Field(default="transfer")
    entities: SimpleTransferEntities | None = Field(default=None)
    missing_fields: list[str] = Field(default_factory=list, alias="missingFields")
    reply: str = Field(default="")
