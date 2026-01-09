"""Conversational intent handler."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.intent_handlers.base import IntentHandler

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.pipeline.routing_context import RoutingContext
    from apps.core.src.agent.orchestrator.services.conversation_responder import ConversationResponder
    from apps.core.src.agent.sub_agents.query.graph import QueryFlowGraph


class ConversationalHandler(IntentHandler):
    """Handles conversational/fallback intent routing."""

    def __init__(
        self,
        conversation_responder: "ConversationResponder",
        query_graph: "QueryFlowGraph | None" = None,
    ):
        self.conversation_responder = conversation_responder
        self.query_graph = query_graph

    def can_handle(self, intent: str) -> bool:
        return intent in ("conversational", "unknown") or True

    @property
    def send_ack_before_handling(self) -> bool:
        return False

    async def handle(self, ctx: "RoutingContext") -> str:
        if self.query_graph and await self.query_graph.has_active_session(ctx.phone_number):
            query_result = await self.query_graph.run(
                ctx.phone_number,
                ctx.text,
                ctx.user_ctx,
            )
            return query_result if isinstance(query_result, str) else "Query completed."

        if ctx.result and ctx.result.response:
            return ctx.result.response

        return await self.conversation_responder.generate_reply(
            ctx.phone_number,
            ctx.text,
            ctx.result,
            ctx.user_ctx,
        )
