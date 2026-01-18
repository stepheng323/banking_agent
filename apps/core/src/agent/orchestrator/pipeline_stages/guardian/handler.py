"""Guardian Handler - Interaction Policy Layer.

Enforces safety policies and routing rules before business logic.
Position: After ClassificationHandler, Before IntentRoutingHandler.
"""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from shared.cache.redis_client import RedisClient
from shared.utils.logging import get_logger
from shared.utils.sanitize import is_suspicious_input

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.transfer import TransferService
    from apps.core.src.agent.graphs.airtime import AirtimeService
    from apps.core.src.agent.orchestrator.pipeline_stages.affirmation.service import FlowContextService

logger = get_logger(__name__)


class GuardianHandler(MessageHandler):
    """
    Interaction Policy Layer: enforces safety and routing rules.

    Responsibilities:
    1. Block dangerous intents (hard block)
    2. Enforce input contracts (e.g., PIN state)
    3. Detect suspicious input patterns
    4. Invalidate auth when intent changes mid-flow
    """

    def __init__(
        self,
        transfer_service: "TransferService" = None,
        airtime_service: "AirtimeService" = None,
        flow_context_service: "FlowContextService" = None,
    ):
        self.transfer_service = transfer_service
        self.airtime_service = airtime_service
        self.flow_context = flow_context_service

    async def can_handle(self, context: MessageContext) -> bool:
        """Always runs to enforce policies."""
        return context.classification_result is not None

    async def handle(self, context: MessageContext) -> MessageContext:
        """Enforce interaction policies."""
        intent = context.classification_result.intent if context.classification_result else None

        if intent == "dangerous":
            logger.warning(
                "guardian_blocked_dangerous",
                phone=context.phone_number,
                text_preview=context.text[:30],
            )
            return context.with_response(
                "I can't process that request.",
                handled=True,
            )
        
        if is_suspicious_input(context.text):
            logger.warning(
                "guardian_blocked_suspicious",
                phone=context.phone_number,
                text_preview=context.text[:50],
            )
            return context.with_response(
                "I couldn't process that message. Please rephrase.",
                handled=True,
            )

        flow_state = None
        if context.conversation_state:
            flow_state = context.conversation_state.get("flow_state")

        if flow_state == "authorizing":
            allowed_intents = {"cancel", "help", "yes", "no"}
            active_flow = context.conversation_state.get("active_flow")
            if active_flow:
                allowed_intents.add(active_flow)

            if intent == "add_task":
                logger.info(
                    "guardian_deferred_add_task",
                    phone=context.phone_number,
                    intent=intent,
                    flow_state=flow_state,
                )
                return context.with_response(
                    "Let's finish this transaction first. You can add another task after.",
                    handled=True,
                )

            if intent and intent not in allowed_intents and intent != "correct":
                logger.info(
                    "guardian_invalidate_for_intent_change",
                    phone=context.phone_number,
                    intent=intent,
                    active_flow=active_flow,
                    flow_state=flow_state,
                )
                
                await self._invalidate_pending_auth(context.phone_number, active_flow)
                
                return context

        if intent == "add_task" and context.conversation_state:
            active_flow = context.conversation_state.get("active_flow")
            if active_flow:
                logger.info(
                    "guardian_deferred_add_task",
                    phone=context.phone_number,
                    intent=intent,
                    active_flow=active_flow,
                )
                return context.with_response(
                    f"I'm in the middle of a {active_flow}. Let's finish it first, then we can add more tasks.",
                    handled=True,
                )

        return context

    async def _invalidate_pending_auth(self, phone_number: str, active_flow: str | None) -> None:
        """Invalidate pending authorization by clearing idempotency key from checkpoint."""
        try:
            redis = RedisClient.get_client()
            
            await redis.delete(f"user:{phone_number}:pending_transfer_flow_token")
            
            flow_checkpoint_map = {
                "transfer": ("transfer:checkpoint:", self.transfer_service),
                "airtime": ("airtime:checkpoint:", self.airtime_service),
            }
            
            if active_flow in flow_checkpoint_map:
                prefix, service = flow_checkpoint_map[active_flow]
                if service:
                    checkpoint_key = f"{prefix}{phone_number}"
                    checkpoint_data = await redis.get(checkpoint_key)
                    if checkpoint_data:
                        import json
                        checkpoint = json.loads(checkpoint_data)
                        if "idempotency_key" in checkpoint:
                            checkpoint["idempotency_key"] = None
                            checkpoint["flow_state"] = "extracting"
                            await redis.set(checkpoint_key, json.dumps(checkpoint))
                            logger.info("guardian_invalidated_auth", phone=phone_number, flow=active_flow)
                
        except Exception as e:
            logger.error("guardian_invalidate_auth_error", phone=phone_number, error=str(e))
