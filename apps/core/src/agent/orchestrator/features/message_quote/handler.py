"""Quote-based transaction handler - detects and handles quoted message intents."""

from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from .service import QuoteService

from shared.utils.logging import get_logger

logger = get_logger(__name__)


QUOTE_INTENTS = {"repeat_transaction", "modify_transaction"}


class QuoteHandler(MessageHandler):
    """
    Handles quote-based transaction requests.
    
    When user quotes a previous message and says "send this again" or
    "but with 5k", this handler detects the intent and routes appropriately.
    """

    def __init__(self, quote_service: QuoteService):
        self.quote_service = quote_service
    
    async def can_handle(self, context: MessageContext) -> bool:
        """
        Can handle if:
        1. User is quoting a message (quoted_message_id present)
        2. Classification detected repeat_transaction or modify_transaction intent
        """
        if not context.classification_result:
            return False
        
        intent = context.classification_result.intent
        if intent not in QUOTE_INTENTS:
            return False
        
        if not context.quoted_message_id:
            return False
        
        return True
    
    async def handle(self, context: MessageContext) -> MessageContext:
        """
        Handle quote-based transaction request.
        
        Uses LLM-generated response from classification for both:
        - Found quotes: confirmation message
        """
        intent = context.classification_result.intent
        
        logger.info("quote_handler_processing",
                   phone=context.phone_number,
                   intent=intent,
                   quoted_message_id=context.quoted_message_id,
                   has_data=context.quoted_message_data is not None)
        
        response = context.classification_result.response
        
        await self.quote_service.initiate_transaction(context)
        return context.with_response(response, handled=True)