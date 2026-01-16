"""Query intent handler."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.pipeline_stages.intent_routing.handlers.base import IntentHandler

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.query import QueryService
    from apps.core.src.agent.orchestrator.pipeline.routing_context import RoutingContext


class QueryHandler(IntentHandler):
    """Handles query/balance check intent routing."""

    def __init__(self, query_service: "QueryService"):
        self.query_service = query_service

    def can_handle(self, intent: str) -> bool:
        return intent == "query"

    @property
    def pausable_flows(self) -> tuple[str, ...]:
        return ("transfer", "airtime")

    @property
    def supports_resume_prompt(self) -> bool:
        return True

    @property
    def send_ack_before_handling(self) -> bool:
        # Don't send ack for continuations
        return False  # We handle this internally

    async def handle(self, ctx: "RoutingContext") -> str:
        query_result = await self.query_service.run_simple(
            ctx.phone_number,
            ctx.text,
            classification_result={
                "intent": ctx.result.intent,
                "user_id": ctx.user_id,
            },
            user_context=ctx.user_ctx,
        )
        return query_result

    async def is_continuation(self, phone_number: str) -> bool:
        """Check if this is a continuation of an existing query session."""
        return await self.query_service.has_active_session(phone_number)
