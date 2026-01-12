"""Intent routing handler - routes to appropriate service based on intent."""

from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.pipeline_stages.intent_routing.router import (
    OrchestratorIntentRouter,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class IntentRoutingHandler(MessageHandler):
    """
    Routes message to appropriate service based on intent.

    This is the fallback handler that always runs if no other handler handled the message.
    """

    def __init__(self, intent_router: OrchestratorIntentRouter):
        self.intent_router = intent_router

    async def can_handle(self, context: MessageContext) -> bool:
        """Always can handle (fallback)."""
        return True

    async def handle(self, context: MessageContext) -> MessageContext:
        """Route to appropriate service."""
        logger.debug("routing")

        response = await self.intent_router.route_intent(
            context.phone_number,
            context.text,
            context.classification_result,
            context.user_context,
            image_data=context.image_data,
            message_id=context.message_id,
            is_flow_resume=context.is_flow_resume,
        )

        return context.with_response(response, handled=True)
