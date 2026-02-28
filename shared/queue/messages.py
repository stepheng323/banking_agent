"""Message types for queue-based inter-service communication."""

from dataclasses import dataclass
from enum import Enum
from typing import Any

from shared.queue.contracts import get_contract_by_topic


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
    channel: str = "whatsapp"
    error: str | None = None
    extra_data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result: dict[str, Any] = {
            "event_type": self.event_type.value,
            "phone_number": self.phone_number,
            "flow_type": self.flow_type,
            "idempotency_key": self.idempotency_key,
            "success": self.success,
            "channel": self.channel,
        }
        if self.error:
            result["error"] = self.error
        if self.extra_data:
            result["extra_data"] = self.extra_data
        return result


FLOW_EVENTS_QUEUE = get_contract_by_topic("flow_event.process").queue_name
OUTBOX_QUEUE = get_contract_by_topic("notification.send").queue_name
ACTIONABLE_MESSAGES_QUEUE = get_contract_by_topic("actionable_message.send").queue_name
