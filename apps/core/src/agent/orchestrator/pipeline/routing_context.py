"""Routing context for intent handlers."""

from dataclasses import dataclass
from typing import Any

from apps.core.src.agent.orchestrator.models.classification import ClassificationResult


@dataclass
class RoutingContext:
    """Context passed to intent handlers.

    Contains all the information needed to route and handle an intent.
    """

    phone_number: str
    text: str
    result: ClassificationResult
    user_ctx: dict[str, Any]
    image_data: str | None = None
    message_id: str | None = None
    conversation_state: dict[str, Any] | None = None

    @property
    def intent(self) -> str:
        """Get the lowercase intent from classification result."""
        return self.result.intent.lower()

    @property
    def user_id(self) -> str:
        """Get user ID from context."""
        return self.user_ctx.get("user_id", "")

    @property
    def active_flow(self) -> str | None:
        """Get active flow from conversation state."""
        if self.conversation_state:
            return self.conversation_state.get("active_flow")
        return None

    def get_flow_summary(self) -> dict[str, Any]:
        """Extract flow summary from conversation state for pausing."""
        if not self.conversation_state:
            return {}
        return {
            "amount": self.conversation_state.get("amount"),
            "recipient_name": self.conversation_state.get("recipient_name"),
            "recipient_phone": self.conversation_state.get("recipient_phone"),
            "recipient_account": self.conversation_state.get("recipient_account"),
            "network": self.conversation_state.get("network"),
        }
