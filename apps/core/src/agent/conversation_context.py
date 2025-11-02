"""Conversation context tracking for multi-turn conversations."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Optional


@dataclass
class ConversationContext:
    """Tracks active conversation state for multi-turn interactions."""

    active_agent: Literal["query", "transfer", "utility"]
    awaiting_clarification: bool = False
    clarification_type: Optional[str] = None
    last_activity: datetime = field(default_factory=datetime.now)

    TIMEOUT_MINUTES: int = 5

    def is_expired(self) -> bool:
        """Check if conversation context has expired (5 minutes of inactivity)."""
        elapsed = datetime.now() - self.last_activity
        return elapsed > timedelta(minutes=self.TIMEOUT_MINUTES)

    def update_activity(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = datetime.now()

    def set_awaiting_clarification(self, clarification_type: Optional[str] = None) -> None:
        """Set the context to awaiting clarification."""
        self.awaiting_clarification = True
        self.clarification_type = clarification_type
        self.update_activity()

    def clear_awaiting_clarification(self) -> None:
        """Clear the awaiting clarification flag."""
        self.awaiting_clarification = False
        self.clarification_type = None
        self.update_activity()

    def switch_agent(self, new_agent: Literal["query", "transfer", "utility"]) -> None:
        """Switch to a new active agent."""
        # Only clear awaiting_clarification if actually switching to a different agent
        # If staying on the same agent (e.g., continuing a conversation), preserve the flag
        if self.active_agent != new_agent:
            self.clear_awaiting_clarification()
        self.active_agent = new_agent
        self.update_activity()
