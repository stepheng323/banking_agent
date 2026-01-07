"""Data purchase intent handler."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.features.intent_routing.intent_handlers.base import IntentHandler

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.features.intent_routing.routing_context import RoutingContext
    from apps.core.src.agent.sub_agents.data import DataPurchaseGraph


class DataHandler(IntentHandler):
    """Handles data purchase intent routing."""

    def __init__(self, data_graph: "DataPurchaseGraph | None"):
        self.data_graph = data_graph

    def can_handle(self, intent: str) -> bool:
        return intent == "data"

    @property
    def pausable_flows(self) -> tuple[str, ...]:
        return ("transfer", "airtime")

    @property
    def supports_resume_prompt(self) -> bool:
        return True

    async def handle(self, ctx: "RoutingContext") -> str:
        if self.data_graph:
            return await self.data_graph.run(
                ctx.phone_number,
                ctx.text,
                ctx.user_ctx,
            )
        return "Data purchase is not available at the moment. Please try again later."
