from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from apps.chat.src.agent.shared.query_contracts import FocusedReferent, SelectionPayload


class EntityType(str, Enum):
    """Types of entities tracked in context."""

    BENEFICIARY = "beneficiary"
    TRANSACTION = "transaction"
    ACCOUNT = "account"
    DATA_PLAN = "data_plan"
    SUPPORT_TICKET = "support_ticket"
    GENERIC = "generic"


class ContextFrameType(str, Enum):
    """Types of context frames."""

    TRANSACTION_LIST = "transaction_list"
    TRANSACTION_DETAIL = "transaction_detail"
    RECEIPT = "receipt"
    BENEFICIARY_LIST = "beneficiary_list"
    ACCOUNT_LIST = "account_list"
    SCHEDULE_LIST = "schedule_list"
    GENERIC = "generic"


class ContextEntity(BaseModel):
    """A single entity within a context frame."""

    entity_type: EntityType
    entity_id: str | None = None
    label: str
    selection_payload: SelectionPayload | None = None
    focused_referent: FocusedReferent | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class ContextFrame(BaseModel):
    """A frame of context representing a set of displayed entities."""

    frame_id: str
    frame_type: ContextFrameType
    items: list[ContextEntity] = Field(default_factory=list)
    focus_index: int = 0
    source_message_id: str | None = None
    created_at_ts: int
    ttl_seconds: int = 600
