"""Types for the agent."""

from enum import Enum
from typing import Any

from pydantic import BaseModel


class IntentType(str, Enum):
    """User intent types."""

    FAQ = "faq"
    SINGLE_ACTION = "single_action"
    MULTI_STEP = "multi_step"
    SMALL_TALK = "small_talk"
    UNKNOWN = "unknown"


class SecurityLevel(str, Enum):
    """Security level for operations."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskStatus(str, Enum):
    """Task status for the agent."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COLLECTION_COMPLETE = "collection_complete"  # All info collected, ready for authorization
    COMPLETED = "completed"
    FAILED = "failed"


class Task(BaseModel):
    """Task for the agent."""

    id: str
    action: str
    status: TaskStatus = TaskStatus.PENDING
    parameters: dict[str, Any] = {}
    depends_on: list[str] = []
    condition: str | None = None
    result: dict[str, Any] | None = None
