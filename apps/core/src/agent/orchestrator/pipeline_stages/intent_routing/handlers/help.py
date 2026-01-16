"""Help intent handler - handles support and FAQ."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.pipeline_stages.intent_routing.handlers.base import IntentHandler

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.faq import FAQService
    from apps.core.src.agent.graphs.support import SupportService
    from apps.core.src.agent.orchestrator.pipeline.routing_context import RoutingContext
    from apps.core.src.agent.orchestrator.pipeline_stages.intent_routing.conversation_responder import (
        ConversationResponder,
    )


class HelpHandler(IntentHandler):
    """Handles support and FAQ intents."""

    INTENTS = {"support", "faq"}

    def __init__(
        self,
        support_service: "SupportService | None",
        faq_service: "FAQService | None",
        conversation_responder: "ConversationResponder",
    ):
        self.support_service = support_service
        self.faq_service = faq_service
        self.conversation_responder = conversation_responder

    def can_handle(self, intent: str) -> bool:
        return intent in self.INTENTS

    async def handle(self, ctx: "RoutingContext") -> str:
        intent = ctx.result.intent.lower()

        if intent == "support":
            return await self._handle_support(ctx)
        if intent == "faq":
            return await self._handle_faq(ctx)
        return "How can I help you?"

    async def _handle_support(self, ctx: "RoutingContext") -> str:
        if self.support_service:
            # Prepare classification result for service
            classification = {
                "user_id": ctx.user_id,
                "intent": ctx.result.intent,
            }
            response = await self.support_service.run_simple(
                phone=ctx.phone_number,
                text=ctx.text,
                classification_result=classification,
                quoted_data={"wa_message_id": ctx.message_id} if ctx.message_id else None,
            )
            if response:
                return response
        return await self._fallback_response(ctx)

    async def _handle_faq(self, ctx: "RoutingContext") -> str:
        if not self.faq_service:
            return await self._fallback_response(ctx)

        response = await self.faq_service.run_simple(
            phone=ctx.phone_number,
            text=ctx.text,
        )

        # Note: We lost the 'should_route_to_support' explicit check here as run_simple returns str.
        # If deeply needed, we'd need to extend FAQService.
        
        return response if response else await self._fallback_response(ctx)

    async def _fallback_response(self, ctx: "RoutingContext") -> str:
        return await self.conversation_responder.generate_reply(
            ctx.phone_number, ctx.text, ctx.result, ctx.user_ctx
        )
