"""Mandate reinitiation handler for expired/cancelled mandates."""

from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from shared.services.onboarding import mandate_service, ServiceResult
from shared.cache.redis_client import RedisClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class MandateReinitiationHandler(MessageHandler):
    """
    Handles mandate reinitiation button responses.
    
    Runs when user clicks the "Reinitiate Mandate" button.
    """
    
    REINITIATE_BUTTON_ID = "reinitiate_mandate"
    
    async def can_handle(self, context: MessageContext) -> bool:
        """Can handle if this is a reinitiate mandate button click."""

        if context.text == self.REINITIATE_BUTTON_ID:
            return True
        
        redis = RedisClient.get_client()
        pending_key = f"mandate:pending_reinitiation:{context.phone_number}"
        pending = await redis.get(pending_key)
        return pending is not None and context.text.lower() in ("yes", "reinitiate", "ok")
    
    async def handle(self, context: MessageContext) -> MessageContext:
        """Handle mandate reinitiation."""
        logger.info("mandate_reinitiation_triggered", phone=context.phone_number)
        
        redis = RedisClient.get_client()
        pending_key = f"mandate:pending_reinitiation:{context.phone_number}"
        account_id = await redis.get(pending_key)
        
        if not account_id:
            accounts = context.user_context.get("accounts", [])
            default_account = next(
                (acc for acc in accounts if acc.get("is_default")),
                accounts[0] if accounts else None
            )
            if default_account:
                account_id = default_account.get("account_id")
        
        if not account_id:
            return context.with_response(
                "⚠️ Could not find your account. Please contact support.",
                handled=True
            )
        
        result = ServiceResult(**await mandate_service.reinitiate_mandate(
            phone_number=context.phone_number,
            account_id=account_id
        ))
        
        await redis.delete(pending_key)
        
        if result.success:
            # Auth message with success header already sent by reinitiate_mandate
            return context.with_response("", handled=True)
        else:
            return context.with_response(
                f"⚠️ {result.error}",
                handled=True
            )

