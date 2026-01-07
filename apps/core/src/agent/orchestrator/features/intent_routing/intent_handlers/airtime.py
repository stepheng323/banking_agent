"""Airtime intent handler."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.features.intent_routing.intent_handlers.base import IntentHandler

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.features.intent_routing.routing_context import RoutingContext
    from apps.core.src.agent.sub_agents.airtime import AirtimeService


class AirtimeHandler(IntentHandler):
    """Handles airtime intent routing."""

    def __init__(self, airtime_service: "AirtimeService"):
        self.airtime_service = airtime_service

    def can_handle(self, intent: str) -> bool:
        return intent == "airtime"

    @property
    def pausable_flows(self) -> tuple[str, ...]:
        return ("transfer", "data")

    @property
    def supports_resume_prompt(self) -> bool:
        return True

    async def handle(self, ctx: "RoutingContext") -> str:
        classification_dict = (
            ctx.result.model_dump()
            if hasattr(ctx.result, "model_dump")
            else {
                "intent": ctx.result.intent,
                "is_cancellation": ctx.result.is_cancellation,
                "confidence": ctx.result.confidence,
            }
        )

        return await self.airtime_service.run_simple(
            ctx.phone_number,
            ctx.text,
            classification_dict,
        )
