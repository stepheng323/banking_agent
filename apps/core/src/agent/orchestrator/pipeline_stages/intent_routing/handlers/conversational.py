"""Conversational intent handler."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.pipeline_stages.intent_routing.handlers.base import IntentHandler

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.query import QueryService
    from apps.core.src.agent.orchestrator.pipeline.routing_context import RoutingContext
    from apps.core.src.agent.orchestrator.pipeline_stages.intent_routing.conversation_responder import (
        ConversationResponder,
    )


class ConversationalHandler(IntentHandler):
    """Handles conversational/fallback intent routing."""

    def __init__(
        self,
        conversation_responder: "ConversationResponder",
        query_service: "QueryService | None" = None,
    ):
        self.conversation_responder = conversation_responder
        self.query_service = query_service

    def can_handle(self, intent: str) -> bool:
        return intent in ("conversational", "unknown") or True

    @property
    def send_ack_before_handling(self) -> bool:
        return False

    async def handle(self, ctx: "RoutingContext") -> str:
        if self.query_service and await self.query_service.has_active_session(ctx.phone_number):
            query_result = await self.query_service.run_simple(
                ctx.phone_number,
                ctx.text,
                user_context=ctx.user_ctx,
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
