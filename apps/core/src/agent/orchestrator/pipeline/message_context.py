"""Message context for pipeline processing."""

from dataclasses import dataclass, field
from typing import Any

from apps.core.src.agent.orchestrator.models.classification import ClassificationResult
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
    quoted_message_id: str | None = None
    quoted_message_data: dict[str, Any] | None = None

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
        return MessageContext(
            phone_number=self.phone_number,
            text=self.text,
            message_id=self.message_id,
            image_data=self.image_data,
            quoted_message_id=self.quoted_message_id,
            quoted_message_data=self.quoted_message_data,
            user_context=self.user_context,
            conversation_state=self.conversation_state,
            last_response=self.last_response,
            suggestion_data=self.suggestion_data,
            suggestion_context=self.suggestion_context,
            classification_result=self.classification_result,
            has_active_queue=self.has_active_queue,
            current_task_id=self.current_task_id,
            planner_output=self.planner_output,
            response=response,
            handled=handled,
            is_flow_resume=self.is_flow_resume,
        )

    def update(self, **kwargs) -> "MessageContext":
        """Create new context with updated fields."""
        current_dict = {
            "phone_number": self.phone_number,
            "text": self.text,
            "message_id": self.message_id,
            "image_data": self.image_data,
            "quoted_message_id": self.quoted_message_id,
            "quoted_message_data": self.quoted_message_data,
            "user_context": self.user_context,
            "conversation_state": self.conversation_state,
            "last_response": self.last_response,
            "suggestion_data": self.suggestion_data,
            "suggestion_context": self.suggestion_context,
            "classification_result": self.classification_result,
            "has_active_queue": self.has_active_queue,
            "current_task_id": self.current_task_id,
            "planner_output": self.planner_output,
            "response": self.response,
            "handled": self.handled,
            "is_flow_resume": self.is_flow_resume,
        }
        current_dict.update(kwargs)
        return MessageContext(**current_dict)
