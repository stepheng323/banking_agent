"""Message context for pipeline processing."""

from dataclasses import dataclass, field
from typing import Optional, Any

from apps.core.src.agent.models import ClassificationResult, PlannerOutput


@dataclass
class MessageContext:
    """
    Immutable context for message processing through the pipeline.
    
    Each handler receives this context, processes it, and returns an updated version.
    """
    
    # Input
    phone_number: str
    text: str
    message_id: str
    
    # Loaded user data
    user_context: dict[str, Any] = field(default_factory=dict)
    conversation_state: Optional[dict[str, Any]] = None
    last_response: Optional[str] = None
    suggestion_data: Optional[str] = None
    suggestion_context: Optional[dict[str, Any]] = None
    
    # Classification
    classification_result: Optional[ClassificationResult] = None
    
    # Queue state
    has_active_queue: bool = False
    current_task_id: Optional[str] = None
    planner_output: Optional[PlannerOutput] = None
    
    # Response
    response: Optional[str] = None
    handled: bool = False
    
    @property
    def intent(self) -> Optional[str]:
        """Get intent from classification result."""
        if self.classification_result:
            return self.classification_result.intent.lower()
        return None
    
    @property
    def is_cancellation(self) -> bool:
        """Check if message is a cancellation request."""
        if self.classification_result:
            return (
                self.classification_result.intent.lower() == "cancel" or
                self.classification_result.is_cancellation is True
            )
        return False
    
    def with_response(self, response: str, handled: bool = True) -> "MessageContext":
        """Create new context with response set."""
        return MessageContext(
            phone_number=self.phone_number,
            text=self.text,
            message_id=self.message_id,
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
        )
    
    def update(self, **kwargs) -> "MessageContext":
        """Create new context with updated fields."""
        current_dict = {
            "phone_number": self.phone_number,
            "text": self.text,
            "message_id": self.message_id,
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
        }
        current_dict.update(kwargs)
        return MessageContext(**current_dict)
