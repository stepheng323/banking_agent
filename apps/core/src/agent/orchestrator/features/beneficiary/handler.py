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
    NEGATIVE_RESPONSES = {"no", "nope", "nah", "don't", "dont", "skip", "cancel", "nevermind", "never mind"}
    
    def __init__(self, beneficiary_handler: OrchestratorBeneficiaryHandler):
        self.beneficiary_handler = beneficiary_handler
    
    async def can_handle(self, context: MessageContext) -> bool:
        """Can handle if there's suggestion context."""
        return context.suggestion_context is not None
    
    async def handle(self, context: MessageContext) -> MessageContext:
        """Handle beneficiary response."""
        if not context.classification_result or not context.suggestion_context:
            return context

        text_lower = context.text.lower().strip()
        if text_lower in self.NEGATIVE_RESPONSES or any(neg in text_lower for neg in self.NEGATIVE_RESPONSES):
            redis_client = RedisClient.get_client()
            suggestion_key = f"user:{context.phone_number}:beneficiary_suggestion"
            await redis_client.delete(suggestion_key)
            
            conversation_state_key = f"user:{context.phone_number}:conversation_state"
            await redis_client.delete(conversation_state_key)
            
            await redis_client.delete(f"user:{context.phone_number}:transfer_session_start")
            await redis_client.delete(f"user:{context.phone_number}:airtime_session_start")
            
            # Use classifier's response if available (LLM already generated it)
            response = context.classification_result.response or "No worries! Anything else I can help with?"
            return context.with_response(response, handled=True)

        if context.intent in self.TRANSACTION_INTENTS:
            suggestion_key = f"user:{context.phone_number}:beneficiary_suggestion"
            redis_client = RedisClient.get_client()
            await redis_client.delete(suggestion_key)
            
            return context.update(suggestion_context=None)
        
        response = await self.beneficiary_handler.handle_beneficiary_response(
            context.phone_number,
            context.text,
            context.classification_result,
            context.suggestion_context
        )
        
        if response:
            return context.with_response(response, handled=True)
        
        return context
