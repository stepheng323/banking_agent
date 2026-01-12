"""Help intent handler - handles support and FAQ."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.pipeline_stages.intent_routing.handlers.base import IntentHandler

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.faq import FAQFlowGraph
    from apps.core.src.agent.graphs.support.graph import SupportFlowGraph
    from apps.core.src.agent.orchestrator.pipeline.routing_context import RoutingContext
    from apps.core.src.agent.orchestrator.pipeline_stages.intent_routing.conversation_responder import (
        ConversationResponder,
    )


class HelpHandler(IntentHandler):
    """Handles support and FAQ intents."""

    INTENTS = {"support", "faq"}

    def __init__(
        self,
        support_graph: "SupportFlowGraph | None",
        faq_graph: "FAQFlowGraph | None",
        conversation_responder: "ConversationResponder",
    ):
        self.support_graph = support_graph
        self.faq_graph = faq_graph
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
        if self.support_graph:
            response = await self.support_graph.run(
                phone_number=ctx.phone_number,
                message=ctx.text,
                user_id=ctx.user_id,
                message_id=ctx.message_id or "",
            )
            if response is not None:
                return response
        return await self._fallback_response(ctx)

    async def _handle_faq(self, ctx: "RoutingContext") -> str:
        if not self.faq_graph:
            return await self._fallback_response(ctx)

        faq_result = await self.faq_graph.run(
            phone_number=ctx.phone_number,
            message=ctx.text,
            message_id=ctx.message_id or "",
        )

        response = faq_result.get("response", "")

        if faq_result.get("should_route_to_support") and self.support_graph:
            support_response = await self.support_graph.run(
                phone_number=ctx.phone_number,
                message=ctx.text,
                user_id=ctx.user_id,
                message_id=ctx.message_id or "",
            )
            if support_response is not None:
                return support_response

        return response if response else await self._fallback_response(ctx)

    async def _fallback_response(self, ctx: "RoutingContext") -> str:
        return await self.conversation_responder.generate_reply(
            ctx.phone_number, ctx.text, ctx.result, ctx.user_ctx
        )
