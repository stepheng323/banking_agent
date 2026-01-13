"""Intent-based routing for the orchestrator."""

import asyncio
import traceback
from typing import Any

from apps.core.src.agent.orchestrator.models.classification import ClassificationResult
from apps.core.src.agent.orchestrator.pipeline.routing_context import RoutingContext
from apps.core.src.agent.orchestrator.pipeline_stages.intent_routing.deps import IntentRouterDependencies
from apps.core.src.agent.orchestrator.pipeline_stages.intent_routing.handlers import (
    AccountsHandler,
    ConversationalHandler,
    QueryHandler,
)
from apps.core.src.agent.orchestrator.pipeline_stages.intent_routing.handlers.base import IntentHandler
from apps.core.src.agent.orchestrator.pipeline_stages.intent_routing.handlers.help import HelpHandler
from apps.core.src.agent.orchestrator.pipeline_stages.intent_routing.handlers.transaction import TransactionHandler
from apps.core.src.agent.shared.account_validation.mandate_validator import validate_mandate_status
from shared.types.planner import PlannerOutput
from shared.utils.logging import get_logger

logger = get_logger(__name__)

UNRESTRICTED_INTENTS = {
    "manage_accounts",
    "faq",
    "conversational",
    "cancel",
    "yes",
    "no",
    "support",
}

# Intent locking and confidence gating thresholds
MONEY_FLOWS = {"transfer", "airtime", "data"}
INTENT_LOCK_CONFIDENCE_THRESHOLD = 0.85
CONFIDENCE_REPHRASE_THRESHOLD = 0.50
CONFIDENCE_CONFIRM_THRESHOLD = 0.70


class OrchestratorIntentRouter:
    """Routes intents to appropriate services using handler pattern."""

    def __init__(self, deps: "IntentRouterDependencies") -> None:
        self.task_queue_service = deps.task_queue_service
        self.task_planner = deps.task_planner
        self.context_manager = deps.context_manager
        self.whatsapp_client = deps.whatsapp_client
        self.flow_context_service = deps.flow_context_service

        self._handlers: list[IntentHandler] = [
            TransactionHandler(deps.transfer_service, deps.airtime_service, deps.data_graph),
            QueryHandler(deps.query_graph, deps.support_graph),
            HelpHandler(deps.support_graph, deps.faq_graph, deps.conversation_responder),
            AccountsHandler(deps.account_management_service, deps.query_graph),
            ConversationalHandler(deps.conversation_responder, deps.query_graph),
        ]

    def _get_handler(self, intent: str) -> IntentHandler | None:
        """Find handler for the given intent."""
        for handler in self._handlers:
            if handler.can_handle(intent):
                return handler
        return None

    async def _check_account_readiness(self, phone_number: str) -> tuple[bool, str | None]:
        """Check if user has at least one ready account.

        Returns:
            Tuple of (has_ready_account, mandate_message)
        """
        try:
            accounts = await self.context_manager.get_user_accounts(phone_number)
            if not accounts:
                # Edge case: onboarded user with no accounts (shouldn't normally happen)
                return (
                    False,
                    (
                        "⚠️ Your account authorization is pending.\\n\\n"
                        "Please complete the onboarding process to link your bank account."
                    ),
                    None,
                )

            for account in accounts:
                is_valid, _, _ = validate_mandate_status(account)
                if is_valid:
                    return True, None, None

            default_account = next((acc for acc in accounts if acc.get("is_default")), accounts[0])
            _, error_message, metadata = validate_mandate_status(default_account)

            if metadata and metadata.get("needs_reinitiation"):
                try:
                    from shared.services.onboarding import mandate_service

                    account_id = str(default_account.get("account_id") or default_account.get("id"))
                    logger.info("auto_reinitiating_mandate", phone=phone_number, account_id=account_id)

                    heads_up_msg = (
                        "Your account authorization had expired.\n\n"
                        "I have automatically started a new authorization for you. "
                        "Please check the message below for instructions to complete it."
                    )
                    await self.whatsapp_client.send_text(phone_number, heads_up_msg)

                    result = await mandate_service.reinitiate_mandate(phone_number, account_id)

                    if not result.get("success"):
                        logger.error("auto_reinitiation_failed", error=result.get("error"))
                        return False, error_message, metadata

                    return False, "AUTO_REINITIATED_SUCCESS", metadata
                except Exception as e:
                    logger.error("auto_reinitiation_failed", error=str(e))
                    return False, error_message, metadata

            return False, error_message, metadata
        except Exception as e:
            logger.error("account_readiness_check_error", error=str(e))
            return True, None, None  # Fail open to avoid blocking users

    def _generate_task_acknowledgment(self, planner_output: PlannerOutput) -> str:
        """Generate friendly acknowledgment message for multi-task requests."""
        task_count = len(planner_output.tasks)
        normalized = planner_output.normalized_instruction

        first_task_type = "task"
        recipient_name = None
        if planner_output.tasks:
            first_task = planner_output.tasks[0]
            executor = first_task.executor
            if executor == "transfer":
                first_task_type = "transfer"
                params = first_task.parameters or {}
                recipient_name = params.get("recipient")
            elif executor == "airtime":
                first_task_type = "airtime purchase"
            elif executor == "query":
                first_task_type = "query"

        if task_count > 1:
            if recipient_name:
                return (
                    f"I'll help you {normalized.lower()}.\n"
                    f"I'll process these one at a time.\n\n"
                    f"Let's start with the transfer to {recipient_name}."
                )
            return (
                f"I'll help you {normalized.lower()}.\n"
                f"I'll process these one at a time.\n\n"
                f"Let's start with the first {first_task_type}."
            )
        return f"I'll help you {normalized.lower()}.\nLet's get started."

    async def _handle_multi_task(
        self,
        ctx: RoutingContext,
    ) -> str:
        """Handle multi-task/mixed intent requests."""
        try:
            planner_output = await self.task_planner.plan_tasks(ctx.phone_number, ctx.text)
            logger.info("multi_task_planned", task_count=len(planner_output.tasks))

            if planner_output.tasks:
                await self.task_queue_service.create_task_queue(ctx.phone_number, planner_output)
                acknowledgment = self._generate_task_acknowledgment(planner_output)

                await self.whatsapp_client.send_text(ctx.phone_number, acknowledgment, message_id=ctx.message_id)
                asyncio.create_task(self.context_manager.save_last_response(ctx.phone_number, acknowledgment))

                next_task_response = await self.task_planner.handle_next_task(ctx.phone_number, ctx.text)
                if next_task_response and next_task_response.strip():
                    await self.whatsapp_client.send_text(
                        ctx.phone_number, next_task_response, message_id=ctx.message_id
                    )
                    asyncio.create_task(self.context_manager.save_last_response(ctx.phone_number, next_task_response))
                return ""  # Prevent duplicate from message_consumer
        except Exception:
            logger.error("multi_task_error", exc_info=True)
            traceback.print_exc()
        return ""

    async def _pause_if_needed(
        self,
        ctx: RoutingContext,
        pausable_flows: tuple[str, ...],
    ) -> None:
        """Pause active flow if it's in the pausable list."""
        if not pausable_flows or not ctx.active_flow:
            return

        if ctx.active_flow == ctx.intent:
            return

        if ctx.active_flow in pausable_flows:
            await self.flow_context_service.pause_flow(
                ctx.phone_number,
                ctx.active_flow,
                ctx.intent,
                ctx.get_flow_summary(),
            )

    async def _send_ack(self, ctx: RoutingContext) -> None:
        """Send acknowledgment message if present."""
        if ctx.result.response:
            await self.whatsapp_client.send_text(
                ctx.phone_number,
                ctx.result.response,
                message_id=ctx.message_id,
            )

    async def _append_resume_prompt(self, response: str, phone_number: str) -> str:
        """Append resume prompt if there's a paused flow."""
        resume_prompt = await self.flow_context_service.generate_resume_prompt(phone_number)
        if resume_prompt:
            return f"{response}\n\n{resume_prompt}"
        return response

    async def route_intent(
        self,
        phone_number: str,
        text: str,
        result: ClassificationResult,
        user_ctx: dict[str, Any],
        image_data: str | None = None,
        message_id: str | None = None,
        is_flow_resume: bool = False,
    ) -> str:
        """
        Route intent to appropriate service.

        Args:
            phone_number: User's phone number
            text: User's message
            result: Classification result
            user_ctx: User context
            image_data: Optional base64 image data
            message_id: Optional message ID for typing indicator
            is_flow_resume: True if this is resuming a paused flow

        Returns:
            Response string
        """
        conversation_state = await self.context_manager.get_conversation_state(phone_number)
        ctx = RoutingContext(
            phone_number=phone_number,
            text=text,
            result=result,
            user_ctx=user_ctx,
            image_data=image_data,
            message_id=message_id,
            conversation_state=conversation_state,
        )

        has_ready, mandate_message, metadata = await self._check_account_readiness(phone_number)

        if mandate_message == "AUTO_REINITIATED_SUCCESS":
            return ""

        if ctx.intent not in UNRESTRICTED_INTENTS:
            if not has_ready:
                warning_count = await self.context_manager.get_mandate_warning_count(phone_number)

                if warning_count > 0:
                    import random

                    await self.context_manager.increment_mandate_warning_count(phone_number)

                    if metadata and metadata.get("awaiting_nibss"):
                        patience_reminders = [
                            "I see your transfer! We're just waiting for NIBSS to verify it. Hang tight! 🕒",
                            "Your account is almost ready! NIBSS verification usually takes a few minutes.",
                            "We've received your transfer. Account activation is in progress. Please check back soon!",
                        ]
                        return random.choice(patience_reminders)
                    else:
                        action_reminders = [
                            "I know you're eager to proceed, but I really need you to complete that ₦50 transfer first! 🙏",
                            "I can't process any transactions until your account is verified. Please send the ₦50 to the account above.",
                            "Still waiting on that validation transfer! We can't move forward without it.",
                            "Please help me help you! Complete the ₦50 transfer so we can activate your account. 🚀",
                        ]
                        return random.choice(action_reminders)

                await self.context_manager.increment_mandate_warning_count(phone_number)
                return mandate_message or "Please link an account to continue."

        is_multiple = result.is_complex and "multiple" in result.complexity_reason.lower()
        if ctx.intent == "mixed" or is_multiple:
            return await self._handle_multi_task(ctx)

        handler = self._get_handler(ctx.intent)
        if not handler:
            logger.warning("no_handler_found", intent=ctx.intent)
            handler = self._handlers[-1]  # Fallback to conversational

        # INTENT LOCKING: Prevent accidental flow switching for money transactions
        # If user is mid-flow and LLM detects a different money intent with low confidence,
        # ask for confirmation before switching

        if (
            ctx.active_flow in MONEY_FLOWS
            and ctx.intent in MONEY_FLOWS
            and ctx.active_flow != ctx.intent
            and ctx.result.confidence < INTENT_LOCK_CONFIDENCE_THRESHOLD
        ):
            summary = ctx.get_flow_summary_text()
            active_flow_display = ctx.active_flow.replace("_", " ")
            new_intent_display = ctx.intent.replace("_", " ")

            logger.info(
                "intent_lock_triggered",
                phone=phone_number,
                active_flow=ctx.active_flow,
                new_intent=ctx.intent,
                confidence=ctx.result.confidence,
            )

            lock_response = (
                f"We were in the middle of a {active_flow_display}"
                f"{f' ({summary})' if summary else ''}.\n\n"
                f"Did you want to cancel that and start {new_intent_display} instead?\n"
                f"Reply 'yes' to switch, or continue with your {active_flow_display}."
            )
            return lock_response

        # CONFIDENCE GATING: Ask for confirmation on low-confidence classifications

        if ctx.intent in MONEY_FLOWS:
            if ctx.result.confidence < CONFIDENCE_REPHRASE_THRESHOLD:
                logger.info(
                    "confidence_gate_rephrase",
                    phone=phone_number,
                    intent=ctx.intent,
                    confidence=ctx.result.confidence,
                )
                return "I'm not quite sure I understood. Could you rephrase what you'd like to do?"

            if ctx.result.confidence < CONFIDENCE_CONFIRM_THRESHOLD and not ctx.active_flow:
                logger.info(
                    "confidence_gate_confirm",
                    phone=phone_number,
                    intent=ctx.intent,
                    confidence=ctx.result.confidence,
                )
                intent_display = ctx.intent.replace("_", " ")
                return f"Just to confirm: you want to {intent_display}?"

        await self._pause_if_needed(ctx, handler.pausable_flows)

        if handler.send_ack_before_handling and ctx.result.response and not is_flow_resume:
            await self._send_ack(ctx)

        response = await handler.handle(ctx)

        if ctx.intent in UNRESTRICTED_INTENTS and ctx.intent != "conversational" and not has_ready and mandate_message:
            if "Transfer ₦50" not in response and "awaiting_nibss" not in response:
                response = f"{response}\n\n{mandate_message}"

        if handler.supports_resume_prompt and not is_flow_resume:
            response = await self._append_resume_prompt(response, phone_number)

        return response
