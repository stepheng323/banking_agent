"""Support intent handler."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.features.intent_routing.intent_handlers.base import IntentHandler

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.features.intent_routing.routing_context import RoutingContext
    from apps.core.src.agent.orchestrator.services.conversation_responder import ConversationResponder
    from apps.core.src.agent.sub_agents.support.graph import SupportFlowGraph


class SupportHandler(IntentHandler):
    """Handles support intent routing."""

    def __init__(
        self,
        support_graph: "SupportFlowGraph | None",
        conversation_responder: "ConversationResponder",
    ):
        self.support_graph = support_graph
        self.conversation_responder = conversation_responder

    def can_handle(self, intent: str) -> bool:
        return intent == "support"

    async def handle(self, ctx: "RoutingContext") -> str:
        if self.support_graph:
            response = await self.support_graph.run(
                phone_number=ctx.phone_number,
                message=ctx.text,
                user_id=ctx.user_id,
                message_id=ctx.message_id or "",
            )

            if response is None:
                # Fallback to conversation responder
                return await self.conversation_responder.generate_reply(
                    ctx.phone_number,
                    ctx.text,
                    ctx.result,
                    ctx.user_ctx,
                )
            return response

        return "Support is temporarily unavailable. Please try again later."
