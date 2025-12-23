"""Funding confirmation handler for multi-debit responses."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from shared.services.affirmation import AffirmationService
from shared.utils.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from apps.core.src.agent.sub_agents.transfer import TransferService


class FundingConfirmationHandler(MessageHandler):
    """
    Handles yes/no responses to multi-debit funding confirmation.
    
    Runs when user has an active confirming_funding state and responds
    with approval, rejection, or custom funding ratio.
    """
    
    def __init__(self, transfer_service: "TransferService"):
        self.transfer_service = transfer_service
    
    async def can_handle(self, context: MessageContext) -> bool:
        """Can handle if user is in confirming_funding state."""
        if not context.conversation_state:
            return False
        return context.conversation_state.get("flow_state") == "confirming_funding"
    
    async def handle(self, context: MessageContext) -> MessageContext:
        """Handle funding confirmation response using AffirmationService."""
        result = AffirmationService.classify_sync(context.text)
        
        if result.is_approval:
            logger.info("funding_confirmation_approved", phone=context.phone_number)
            response = await self.transfer_service.run_simple(
                context.phone_number,
                "yes",
                {"intent": "transfer", "funding_approved": True}
            )
            return context.with_response(response, handled=True)
        
        if result.is_rejection:
            logger.info("funding_confirmation_rejected", phone=context.phone_number)
            response = await self.transfer_service.run_simple(
                context.phone_number,
                "cancel",
                {"intent": "transfer", "funding_rejected": True}
            )
            return context.with_response(response, handled=True)
        
        logger.info("funding_confirmation_unclear", phone=context.phone_number, text=context.text)
        response = (
            "I'm not sure if you want to proceed. Please reply:\n"
            "• *yes* to approve the funding plan\n"
            "• *no* to cancel the transfer"
        )
        return context.with_response(response, handled=True)
