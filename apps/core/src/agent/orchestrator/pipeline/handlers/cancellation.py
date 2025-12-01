"""Cancellation handler - handles cancellation requests."""

from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.cancellation_handler import OrchestratorCancellationHandler


class CancellationHandler(MessageHandler):
    """
    Handles cancellation requests.
    
    Runs when user wants to cancel current operation.
    """
    
    def __init__(self, cancellation_handler: OrchestratorCancellationHandler):
        self.cancellation_handler = cancellation_handler
    
    async def can_handle(self, context: MessageContext) -> bool:
        """Can handle if message is a cancellation request."""
        return context.is_cancellation
    
    async def handle(self, context: MessageContext) -> MessageContext:
        """Handle cancellation."""
        response = await self.cancellation_handler.handle_cancellation(
            context.phone_number,
            context.text,
            context.classification_result,
            context.conversation_state
        )
        
        if response:
            return context.with_response(response, handled=True)
        
        # If no response, continue to next handler
        return context
