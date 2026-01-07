"""FAQ intent handler."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.features.intent_routing.intent_handlers.base import IntentHandler

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.features.intent_routing.routing_context import RoutingContext
    from apps.core.src.agent.orchestrator.services.conversation_responder import ConversationResponder
    from apps.core.src.agent.sub_agents.faq import FAQFlowGraph
    from apps.core.src.agent.sub_agents.support.graph import SupportFlowGraph


class FAQHandler(IntentHandler):
    """Handles FAQ intent routing."""

    def __init__(
        self,
        faq_graph: "FAQFlowGraph | None",
        support_graph: "SupportFlowGraph | None",
        conversation_responder: "ConversationResponder",
    ):
        self.faq_graph = faq_graph
        self.support_graph = support_graph
        self.conversation_responder = conversation_responder

    def can_handle(self, intent: str) -> bool:
        return intent == "faq"

    async def handle(self, ctx: "RoutingContext") -> str:
        if not self.faq_graph:
            return await self.conversation_responder.generate_reply(
                ctx.phone_number,
                ctx.text,
                ctx.result,
                ctx.user_ctx,
            )

        faq_result = await self.faq_graph.run(
            phone_number=ctx.phone_number,
            message=ctx.text,
            message_id=ctx.message_id or "",
        )

        response = faq_result.get("response", "")

        # Route to support if FAQ indicates it
        if faq_result.get("should_route_to_support") and self.support_graph:
            support_response = await self.support_graph.run(
                phone_number=ctx.phone_number,
                message=ctx.text,
                user_id=ctx.user_id,
                message_id=ctx.message_id or "",
            )
            if support_response is not None:
                return support_response

        return response
