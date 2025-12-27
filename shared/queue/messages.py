"""Message types for queue-based inter-service communication."""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class FlowEventType(str, Enum):
    """Types of flow events."""

    PIN_VERIFIED = "pin_verified"
    PIN_FAILED = "pin_failed"
    FLOW_COMPLETED = "flow_completed"
    FLOW_ERROR = "flow_error"


@dataclass
class FlowEvent:
    """Event published when a flow completes (e.g., PIN verification)."""

    event_type: FlowEventType
    phone_number: str
    flow_type: str  # "transfer", "airtime", "batch"
    idempotency_key: str
    success: bool
    error: str | None = None
    extra_data: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "event_type": self.event_type.value,
            "phone_number": self.phone_number,
            "flow_type": self.flow_type,
            "idempotency_key": self.idempotency_key,
            "success": self.success,
        }
        if self.error:
            result["error"] = self.error
        if self.extra_data:
            result["extra_data"] = self.extra_data
        return result


# Queue name constants
FLOW_EVENTS_QUEUE = "banking:flow_events"
