"""Fresh start handler - detects and handles fresh start messages."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.features.context.service import OrchestratorContextManager

if TYPE_CHECKING:
    from apps.core.src.agent.transfer import TransferService


class FreshStartHandler(MessageHandler):
    """
    Detects fresh start messages (hi, hello, thanks, etc.) and clears stale state.
    
    Only runs when there's NO active queue but there IS conversation state.
    """
    
    FRESH_START_INDICATORS = [
        "hi", "hello", "hey", 
        "good morning", "good afternoon", "good evening",
        "thanks", "thank you", 
        "bye", "goodbye"
    ]
    
    def __init__(
        self,
        context_manager: OrchestratorContextManager,
        transfer_service: "TransferService",
    ):
        self.context_manager = context_manager
        self.transfer_service = transfer_service
    
    async def can_handle(self, context: MessageContext) -> bool:
        """
        Can handle if:
        - No active queue
        - Has conversation state (stale)
        - Message is a fresh start indicator
        """
        if context.has_active_queue or not context.conversation_state:
            return False
        
        text_lower = context.text.strip().lower()
        return any(
            text_lower.startswith(indicator) or text_lower == indicator
            for indicator in self.FRESH_START_INDICATORS
        )
    
    async def handle(self, context: MessageContext) -> MessageContext:
        """Clear stale conversation state and transfer checkpoint."""
        await self.context_manager.clear_conversation_state(context.phone_number)
        await self.transfer_service.graph.clear_checkpoint(context.phone_number)
        
        print(f"✅ Cleared stale state for fresh start message: {context.text}")
        
        # Return context with cleared conversation_state
        return context.update(conversation_state=None)
