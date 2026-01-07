"""Transfer intent handler."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.features.intent_routing.intent_handlers.base import IntentHandler

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.features.intent_routing.routing_context import RoutingContext
    from apps.core.src.agent.sub_agents.transfer import TransferService


class TransferHandler(IntentHandler):
    """Handles transfer intent routing."""

    def __init__(self, transfer_service: "TransferService"):
        self.transfer_service = transfer_service

    def can_handle(self, intent: str) -> bool:
        return intent == "transfer"

    @property
    def pausable_flows(self) -> tuple[str, ...]:
        return ("airtime", "data")

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

        return await self.transfer_service.run_simple(
            ctx.phone_number,
            ctx.text,
            classification_dict,
            image_data=ctx.image_data,
        )
