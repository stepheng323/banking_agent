"""Quote-based transaction handler - detects and handles quoted message intents."""

from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from shared.clients.whatsapp.client import WhatsAppClient
from shared.utils.logging import get_logger

from apps.core.src.agent.orchestrator.pipeline_stages.quote.service import QuoteService

logger = get_logger(__name__)


QUOTE_INTENTS = {"repeat_transaction", "modify_transaction"}


class QuoteHandler(MessageHandler):
    """
    Handles quote-based transaction requests.

    When user quotes a previous message and says "send this again" or
    "but with 5k", this handler detects the intent and routes appropriately.
    """

    def __init__(self, quote_service: QuoteService, whatsapp_client: WhatsAppClient):
        self.quote_service = quote_service
        self.whatsapp_client = whatsapp_client

    async def can_handle(self, context: MessageContext) -> bool:
        """
        Can handle if:
        1. User is quoting a message (quoted_message_id present)
        2. Classification detected repeat_transaction or modify_transaction intent
        """
        if not context.classification_result:
            return False

        intent = context.classification_result.intent
        if intent not in QUOTE_INTENTS or context.quoted_message_id is None:
            return False

        return True

    async def handle(self, context: MessageContext) -> MessageContext:
        """Handle quote-based transaction request."""
        intent = context.classification_result.intent

        logger.info(
            "quote_handler_processing",
            phone=context.phone_number,
            intent=intent,
            quoted_message_id=context.quoted_message_id,
            has_data=context.quoted_message_data is not None,
        )

        response = context.classification_result.response

        if response:
            await self.whatsapp_client.send_text(
                to=context.phone_number, text=response, message_id=context.message_id
            )

        await self.quote_service.initiate_transaction(context)

        return context.with_response("", handled=True)
