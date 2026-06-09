"""Message context for pipeline processing."""

from dataclasses import dataclass, field, replace
from typing import Any

from apps.chat.src.agent.orchestrator.models.classification import ClassificationResult
from shared.database.models import User
from shared.messaging.channels import MessagingChannel, normalize_messaging_channel
from shared.types.planner import PlannerOutput


@dataclass
class MessageContext:
    """
    Immutable context for message processing through the pipeline.

    Each handler receives this context, processes it, and returns an updated version.
    """

    phone_number: str
    text: str
    message_id: str
    image_data: str | None = None
    is_media_input: bool = False
    quoted_message_id: str | None = None
    quoted_message_data: dict[str, Any] | None = None
    channel: MessagingChannel = MessagingChannel.WHATSAPP
    channel_identity: str | None = None
    channel_metadata: dict[str, Any] = field(default_factory=dict)
    resolved_user: User | None = None

    user_context: dict[str, Any] = field(default_factory=dict)
    conversation_state: dict[str, Any] | None = None
    last_response: str | None = None
    suggestion_data: str | None = None
    suggestion_context: dict[str, Any] | None = None

    classification_result: ClassificationResult | None = None

    has_active_queue: bool = False
    current_task_id: str | None = None
    planner_output: PlannerOutput | None = None

    response: str | None = None
    handled: bool = False
    is_flow_resume: bool = False

    def __post_init__(self) -> None:
        self.channel = normalize_messaging_channel(self.channel)

    @property
    def intent(self) -> str | None:
        """Get intent from classification result."""
        if self.classification_result:
            return self.classification_result.intent.lower()
        return None

    @property
    def is_cancellation(self) -> bool:
        """Check if message is a cancellation request."""
        if self.classification_result:
            return (
                self.classification_result.intent.lower() == "cancel"
                or self.classification_result.is_cancellation is True
            )
        return False

    def with_response(self, response: str, handled: bool = True) -> "MessageContext":
        """Create new context with response set."""
        return replace(self, response=response, handled=handled)

    def update(self, **kwargs: Any) -> "MessageContext":
        """Create new context with updated fields."""
        return replace(self, **kwargs)
