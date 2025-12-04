"""Cancellation handler - handles cancellation requests."""

from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from .service import OrchestratorCancellationHandler
from shared.cache.redis_client import RedisClient
from apps.core.src.agent.batch.utils import ExecutionState


class CancellationHandler(MessageHandler):
    """
    Handles cancellation requests.
    
    Runs when user wants to cancel current operation.
    """
    
    def __init__(self, cancellation_handler: OrchestratorCancellationHandler):
        self.cancellation_handler = cancellation_handler
        self.redis_client = RedisClient.get_client()
    
    async def can_handle(self, context: MessageContext) -> bool:
        """Can handle if message is a cancellation request."""
        return context.is_cancellation
    
    async def handle(self, context: MessageContext) -> MessageContext:
        """Handle cancellation."""
        # Check if batch is currently executing
        execution_state = await self.redis_client.get(
            f"queue:{context.phone_number}:execution_state"
        )
        
        if execution_state == ExecutionState.EXECUTING_BATCH:
            # Cancel batch execution by setting flag
            await self.redis_client.set(
                f"queue:{context.phone_number}:cancel_batch",
                "1",
                ex=300  # 5 min
            )
            
            response = (
                "⚠️ Cancelling batch execution...\n\n"
                "✅ Completed tasks will remain completed.\n"
                "⏳ Currently executing tasks will finish.\n"
                "❌ Pending tasks will be cancelled."
            )
            
            return context.with_response(response, handled=True)
        
        # Regular cancellation
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
