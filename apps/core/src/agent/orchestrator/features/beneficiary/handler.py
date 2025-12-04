"""Beneficiary handler - handles beneficiary suggestion responses."""

from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from .service import OrchestratorBeneficiaryHandler
from shared.cache.redis_client import RedisClient


class BeneficiaryHandler(MessageHandler):
    """
    Handles beneficiary suggestion responses.
    
    Runs when there's suggestion context and user is responding to it.
    """
    
    TRANSACTION_INTENTS = {"transfer", "airtime", "data"}
    
    def __init__(self, beneficiary_handler: OrchestratorBeneficiaryHandler):
        self.beneficiary_handler = beneficiary_handler
    
    async def can_handle(self, context: MessageContext) -> bool:
        """Can handle if there's suggestion context."""
        return context.suggestion_context is not None
    
    async def handle(self, context: MessageContext) -> MessageContext:
        """Handle beneficiary response."""
        # If user is starting a new transaction, clear suggestion
        if context.intent in self.TRANSACTION_INTENTS:
            suggestion_key = f"user:{context.phone_number}:beneficiary_suggestion"
            redis_client = RedisClient.get_client()
            await redis_client.delete(suggestion_key)
            
            # Clear suggestion context and continue to next handler
            return context.update(suggestion_context=None)
        
        # Otherwise, handle beneficiary response
        response = await self.beneficiary_handler.handle_beneficiary_response(
            context.phone_number,
            context.text,
            context.classification_result,
            context.suggestion_context
        )
        
        if response:
            return context.with_response(response, handled=True)
        
        # If no response, continue to next handler
        return context
