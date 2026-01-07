"""Query intent handler."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.features.intent_routing.intent_handlers.base import IntentHandler

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.features.intent_routing.routing_context import RoutingContext
    from apps.core.src.agent.sub_agents.query.graph import QueryFlowGraph
    from apps.core.src.agent.sub_agents.support.graph import SupportFlowGraph


class QueryHandler(IntentHandler):
    """Handles query/balance check intent routing."""

    def __init__(
        self,
        query_graph: "QueryFlowGraph",
        support_graph: "SupportFlowGraph | None" = None,
    ):
        self.query_graph = query_graph
        self.support_graph = support_graph

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
        query_result = await self.query_graph.run(
            ctx.phone_number,
            ctx.text,
            ctx.user_ctx,
        )

        # Check if query graph wants to route to support (for issue reports)
        if isinstance(query_result, dict) and query_result.get("route_to_support"):
            if self.support_graph:
                user_id = query_result.get("user_id", ctx.user_id)
                response = await self.support_graph.run(
                    phone_number=ctx.phone_number,
                    message=query_result.get("message", ctx.text),
                    user_id=user_id,
                    transaction=query_result.get("transaction"),
                )
                if response is None:
                    return "I'm having trouble processing your issue. Please try again."
                return response
            return "Support is temporarily unavailable. Please try again later."

        return query_result if isinstance(query_result, str) else "Query completed."

    async def is_continuation(self, phone_number: str) -> bool:
        """Check if this is a continuation of an existing query session."""
        return await self.query_graph.has_active_session(phone_number)
