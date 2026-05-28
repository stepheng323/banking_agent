"""Short-term referent memory models."""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field

ReferentType = Literal["beneficiary", "recipient", "phone", "amount", "source_account", "transaction", "data_plan"]
ReferentSource = Literal["active_flow", "context_frame", "completed_task", "query_result", "stashed_session"]
ResolutionStatus = Literal["none", "resolved", "ambiguous"]

DEFAULT_REFERENT_TTL_SECONDS = 900
STASHED_REFERENT_TTL_SECONDS = 1800
MAX_REFERENT_ITEMS = 20


class ReferentMemoryItem(BaseModel):
    """One safe short-term referent derived from trusted structured state."""

    referent_type: ReferentType
    source: ReferentSource
    label: str | None = None
    entity_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    created_at_ts: int = Field(default_factory=lambda: int(time.time()))
    ttl_seconds: int = DEFAULT_REFERENT_TTL_SECONDS


class ShortTermReferentMemory(BaseModel):
    """Bounded referent memory stored in orchestrator checkpoint state."""

    items: list[ReferentMemoryItem] = Field(default_factory=list)


class ReferentResolution(BaseModel):
    """Resolution result for one referential phrase class."""

    status: ResolutionStatus
    referent_type: ReferentType
    item: ReferentMemoryItem | None = None
    candidates: list[ReferentMemoryItem] = Field(default_factory=list)
    reason: str | None = None
