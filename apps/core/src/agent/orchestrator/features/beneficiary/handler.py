"""Beneficiary handler - handles beneficiary suggestion responses."""

from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from .service import OrchestratorBeneficiaryHandler
from shared.cache.redis_client import RedisClient
from shared.services.affirmation import AffirmationService


class BeneficiaryHandler(MessageHandler):
    """
    Handles beneficiary suggestion responses.
    
    Runs when there's suggestion context and user is responding to it.
    """
    
    TRANSACTION_INTENTS = {"transfer", "airtime", "data"}
    
    POLITE_DECLINE_INTENTS = {"conversational", "unknown"}
    
    def __init__(self, beneficiary_handler: OrchestratorBeneficiaryHandler):
        self.beneficiary_handler = beneficiary_handler
    
    async def can_handle(self, context: MessageContext) -> bool:
        """Can handle if there's suggestion context."""
        return context.suggestion_context is not None
    
    async def _clear_suggestion_context(self, phone_number: str) -> None:
        """Clear all suggestion-related state."""
        redis_client = RedisClient.get_client()
        suggestion_key = f"user:{phone_number}:beneficiary_suggestion"
        await redis_client.delete(suggestion_key)
        
        conversation_state_key = f"user:{phone_number}:conversation_state"
        await redis_client.delete(conversation_state_key)
        
        await redis_client.delete(f"user:{phone_number}:transfer_session_start")
        await redis_client.delete(f"user:{phone_number}:airtime_session_start")
    
    async def handle(self, context: MessageContext) -> MessageContext:
        """Handle beneficiary response."""
        if not context.classification_result or not context.suggestion_context:
            return context

        result = AffirmationService.classify_sync(context.text)
        
        if result.is_rejection:
            await self._clear_suggestion_context(context.phone_number)
            response = context.classification_result.response or "No worries! Anything else I can help with?"
            return context.with_response(response, handled=True)
        
        if context.intent in self.TRANSACTION_INTENTS:
            await self._clear_suggestion_context(context.phone_number)
            return context.update(suggestion_context=None)
        
        intent = context.intent.lower() if context.intent else ""
        if intent in self.POLITE_DECLINE_INTENTS:
            await self._clear_suggestion_context(context.phone_number)
            response = context.classification_result.response or "You're welcome! Let me know if you need anything else."
            return context.with_response(response, handled=True)
        
        response = await self.beneficiary_handler.handle_beneficiary_response(
            context.phone_number,
            context.text,
            context.classification_result,
            context.suggestion_context
        )
        
        if response:
            return context.with_response(response, handled=True)
        
        return context

