"""
Unified Affirmation Handler.

Single handler for all confirmation flows. Detects awaiting_confirmation state,
classifies user response (approve/reject/custom), and routes back to original flow.
"""

from typing import TYPE_CHECKING

from langchain_core.language_models import BaseChatModel

from apps.core.src.agent.orchestrator.models.classification import ClassificationResult
from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from shared.cache.redis_client import RedisClient
from shared.services.affirmation import AffirmationResult, AffirmationService
from shared.services.onboarding import ServiceResult, mandate_service
from shared.utils.logging import get_logger

from .models import ConfirmationContext

logger = get_logger(__name__)

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.features.flow_context.service import FlowContextService
    from apps.core.src.agent.sub_agents.airtime import AirtimeService
    from apps.core.src.agent.sub_agents.transfer import TransferService


class AffirmationHandler(MessageHandler):
    """
    Unified handler for all confirmation flows.

    Replaces:
    - FundingConfirmationHandler
    - FlowResumeHandler
    - MandateReinitiationHandler (partial)

    Flow:
    1. Check if awaiting_confirmation in state
    2. Classify user response with AffirmationService
    3. Route to original flow with user_intent attached
    """

    def __init__(
        self,
        transfer_service: "TransferService",
        airtime_service: "AirtimeService",
        flow_context_service: "FlowContextService",
        llm: BaseChatModel = None,
    ):
        self.transfer_service = transfer_service
        self.airtime_service = airtime_service
        self.flow_context = flow_context_service
        self.llm = llm

    async def can_handle(self, context: MessageContext) -> bool:
        """Can handle if awaiting_confirmation is set or there's a paused flow."""
        # Check for explicit confirmation context
        if context.conversation_state:
            awaiting = context.conversation_state.get("awaiting_confirmation")
            flow_state = context.conversation_state.get("flow_state")
            logger.info(
                "affirmation_can_handle_check",
                awaiting_confirmation=awaiting,
                flow_state=flow_state,
                text=context.text[:30],
            )

            if awaiting:
                # CRITICAL: Check if this is actually a fresh command (Transfer/Airtime)
                # If user is awaiting confirmation but sends a new transfer command, clear and yield
                if context.classification_result and context.classification_result.confidence > 0.8:
                    new_intent = context.classification_result.intent
                    if new_intent in ("transfer", "airtime", "buy_data", "bills"):
                        logger.info("affirmation_handler_yielding_to_new_intent", intent=new_intent)

                        # EXCEPTION: If user is awaiting amount adjustment, DO NOT clear checkpoint
                        # Let the transfer agent handle the update within the existing session
                        flow_state_val = context.conversation_state.get("flow_state")
                        if flow_state_val != "awaiting_amount_adjustment":
                            # Force clear stale state so the new flow starts fresh
                            await self.transfer_service.graph.clear_checkpoint(context.phone_number)
                            if hasattr(self.airtime_service, "graph"):
                                await self.airtime_service.graph.clear_checkpoint(context.phone_number)

                            # Clear conversation flags
                            if context.conversation_state:
                                context.conversation_state.pop("awaiting_confirmation", None)
                        else:
                            logger.info("affirmation_handler_preserving_session_for_adjustment")

                        return False

                logger.info("affirmation_handler_will_handle", reason="awaiting_confirmation")
                return True
        else:
            logger.info("affirmation_no_conversation_state", text=context.text[:30])

        # Check for paused flow (flow resume scenario)
        paused = await self.flow_context.get_paused_flow(context.phone_number)
        if paused:
            result = AffirmationService.classify_sync(context.text)
            if result.is_approval:
                logger.info("affirmation_handler_will_handle", reason="paused_flow_approval")
                return True

        # Check for pending mandate reinitiation
        redis = RedisClient.get_client()
        pending_key = f"mandate:pending_reinitiation:{context.phone_number}"
        pending = await redis.get(pending_key)
        if pending:
            result = AffirmationService.classify_sync(context.text)
            if result.is_approval:
                logger.info("affirmation_handler_will_handle", reason="mandate_reinitiation")
                return True

        return False

    async def handle(self, context: MessageContext) -> MessageContext:
        """
        Handle confirmation response.

        Routes to appropriate flow based on confirmation_context.flow_type
        """
        # Classify user response (with LLM fallback for unclear cases)
        result = await AffirmationService.classify(
            context.text,
            context="approve a funding plan or other confirmation",
            use_llm_fallback=True,
            llm=self.llm,
        )

        logger.info(
            "affirmation_handler",
            phone=context.phone_number,
            intent=result.intent,
            confidence=result.confidence,
            text=context.text[:50],
        )

        # Determine flow type from state
        confirmation_ctx = self._get_confirmation_context(context)

        if confirmation_ctx:
            return await self._route_by_context(context, result, confirmation_ctx)

        # Fallback: Check for paused flow
        paused = await self.flow_context.get_paused_flow(context.phone_number)
        if paused:
            return await self._handle_flow_resume(context, result, paused)

        # Fallback: Check for mandate reinitiation
        return await self._handle_mandate_reinitiation(context, result)

    def _get_confirmation_context(self, context: MessageContext) -> ConfirmationContext | None:
        """Extract confirmation context from conversation state."""
        if not context.conversation_state:
            return None

        # Extract confirmation_context from state
        ctx_data = context.conversation_state.get("confirmation_context")
        if ctx_data:
            return ConfirmationContext.from_dict(ctx_data)

        return None

    async def _route_by_context(
        self,
        context: MessageContext,
        result: AffirmationResult,
        confirmation_ctx: ConfirmationContext,
    ) -> MessageContext:
        """Route to appropriate flow based on confirmation context."""

        flow_type = confirmation_ctx.flow_type
        action = confirmation_ctx.action

        classification_dict = {
            "intent": flow_type,
            "user_affirmation": result.intent,
            "user_affirmation_confidence": result.confidence,
            "confirmation_action": action,
        }

        if result.custom_data:
            classification_dict["custom_modification"] = result.custom_data

        if flow_type == "transfer":
            if result.is_approval:
                classification_dict["funding_approved"] = True
            elif result.is_rejection:
                classification_dict["funding_rejected"] = True

            response = await self.transfer_service.run_simple(context.phone_number, context.text, classification_dict)
            return context.with_response(response, handled=True)

        elif flow_type == "airtime":
            response = await self.airtime_service.run_simple(context.phone_number, context.text, classification_dict)
            return context.with_response(response, handled=True)

        elif flow_type == "beneficiary":
            if result.is_rejection:
                redis = RedisClient.get_client()
                await redis.delete(f"user:{context.phone_number}:beneficiary_suggestion")
                await redis.delete(f"user:{context.phone_number}:conversation_state")
                return context.with_response("No worries! Anything else I can help with?", handled=True)
            return context

        elif flow_type == "mandate":
            return await self._handle_mandate_reinitiation(context, result)

        elif flow_type == "flow_resume":
            paused = await self.flow_context.get_paused_flow(context.phone_number)
            if paused:
                return await self._handle_flow_resume(context, result, paused)

        if result.is_unclear:
            prompt = confirmation_ctx.clarification_prompt or (
                "I'm not sure if you want to proceed. Please reply *yes* or *no*."
            )
            return context.with_response(prompt, handled=True)

        return context

    async def _handle_flow_resume(
        self,
        context: MessageContext,
        result: AffirmationResult,
        paused: dict,
    ) -> MessageContext:
        """Handle resuming a paused flow."""
        if not result.is_approval:
            return context

        flow_type = paused.get("flow_type", "")

        # DON'T clear paused flow here - let the subgraph clear it after reading the saved response
        # await self.flow_context.clear_paused_flow(context.phone_number)

        logger.info("flow_resuming", phone=context.phone_number, flow_type=flow_type)

        new_classification = ClassificationResult(
            intent=flow_type,
            is_cancellation=False,
            is_complex=False,
            confidence=0.95,
            response="",
            complexity_reason="Flow resume after interrupt",
        )

        logger.info(f"✅ [AFFIRMATION] Resuming flow with classification: {new_classification}")

        return context.update(
            classification_result=new_classification,
            is_flow_resume=True,
            # Don't set handled=True - we need ActiveQueueHandler to run the graph
        )

    async def _handle_mandate_reinitiation(
        self,
        context: MessageContext,
        result: AffirmationResult,
    ) -> MessageContext:
        """Handle mandate reinitiation approval."""
        redis = RedisClient.get_client()
        pending_key = f"mandate:pending_reinitiation:{context.phone_number}"
        account_id = await redis.get(pending_key)

        # CRITICAL: Only handle if there's actually a pending mandate reinitiation
        # Don't fall back to finding any account - that causes unintended triggers
        if not account_id:
            # No pending mandate reinitiation - don't handle
            return context

        if not result.is_approval:
            await redis.delete(pending_key)
            return context.with_response("Okay, mandate reinitiation cancelled.", handled=True)

        svc_result = ServiceResult(
            **await mandate_service.reinitiate_mandate(phone_number=context.phone_number, account_id=account_id)
        )

        await redis.delete(pending_key)

        if svc_result.success:
            return context.with_response("", handled=True)
        else:
            return context.with_response(f"⚠️ {svc_result.error}", handled=True)
