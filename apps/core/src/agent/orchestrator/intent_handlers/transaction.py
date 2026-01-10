"""Transaction intent handler - handles transfer, airtime, data."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.intent_handlers.base import IntentHandler

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.pipeline.routing_context import RoutingContext
    from apps.core.src.agent.sub_agents.airtime import AirtimeService
    from apps.core.src.agent.sub_agents.data import DataPurchaseGraph
    from apps.core.src.agent.sub_agents.transfer import TransferService


class TransactionHandler(IntentHandler):
    """Handles all transaction intents: transfer, airtime, data."""

    INTENTS = {"transfer", "airtime", "data"}

    def __init__(
        self,
        transfer_service: "TransferService",
        airtime_service: "AirtimeService",
        data_graph: "DataPurchaseGraph | None",
    ):
        self.transfer_service = transfer_service
        self.airtime_service = airtime_service
        self.data_graph = data_graph

    def can_handle(self, intent: str) -> bool:
        return intent in self.INTENTS

    @property
    def pausable_flows(self) -> tuple[str, ...]:
        return ("transfer", "airtime", "data")

    @property
    def supports_resume_prompt(self) -> bool:
        return True

    async def handle(self, ctx: "RoutingContext") -> str:
        intent = ctx.result.intent.lower()
        
        classification_dict = (
            ctx.result.model_dump()
            if hasattr(ctx.result, "model_dump")
            else {
                "intent": ctx.result.intent,
                "is_cancellation": ctx.result.is_cancellation,
                "confidence": ctx.result.confidence,
            }
        )

        if intent == "transfer":
            return await self.transfer_service.run_simple(
                ctx.phone_number, ctx.text, classification_dict, image_data=ctx.image_data
            )

        if intent == "airtime":
            return await self.airtime_service.run_simple(
                ctx.phone_number, ctx.text, classification_dict
            )

        if intent == "data":
            if self.data_graph:
                return await self.data_graph.run(ctx.phone_number, ctx.text, ctx.user_ctx)
            return "Data purchase is not available at the moment."

        return "Unable to process transaction."
