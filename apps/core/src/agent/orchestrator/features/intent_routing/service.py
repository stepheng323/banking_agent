"""Intent-based routing for the orchestrator."""

import asyncio
import traceback
from typing import Any

from apps.core.src.agent.orchestrator.features.context.service import OrchestratorContextManager
from apps.core.src.agent.orchestrator.features.flow_context.service import FlowContextService
from apps.core.src.agent.orchestrator.features.intent_routing.intent_handlers import (
    AccountsHandler,
    AirtimeHandler,
    ConversationalHandler,
    DataHandler,
    FAQHandler,
    IntentHandler,
    QueryHandler,
    SupportHandler,
    TransferHandler,
)
from apps.core.src.agent.orchestrator.features.intent_routing.routing_context import RoutingContext
from apps.core.src.agent.orchestrator.features.task_planning.service import OrchestratorTaskPlanner
from apps.core.src.agent.orchestrator.models.classification import ClassificationResult
from apps.core.src.agent.orchestrator.services.conversation_responder import ConversationResponder
from apps.core.src.agent.sub_agents.account_management.service import AccountManagementService
from apps.core.src.agent.sub_agents.airtime import AirtimeService
from apps.core.src.agent.sub_agents.data import DataPurchaseGraph
from apps.core.src.agent.sub_agents.faq import FAQFlowGraph
from apps.core.src.agent.sub_agents.query.graph import QueryFlowGraph
from apps.core.src.agent.sub_agents.support.graph import SupportFlowGraph
from apps.core.src.agent.sub_agents.transfer import TransferService
from shared.clients.whatsapp.client import WhatsAppClient
from shared.services.task_queue import TaskQueueService
from shared.types.planner import PlannerOutput
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class OrchestratorIntentRouter:
    """Routes intents to appropriate services using handler pattern."""

    def __init__(
        self,
        task_queue_service: TaskQueueService,
        task_planner: OrchestratorTaskPlanner,
        transfer_service: TransferService,
        airtime_service: AirtimeService,
        conversation_responder: ConversationResponder,
        context_manager: OrchestratorContextManager,
        query_graph: QueryFlowGraph,
        data_graph: DataPurchaseGraph | None = None,
        account_management_service: AccountManagementService = None,
        whatsapp_client: WhatsAppClient = None,
        flow_context_service: FlowContextService | None = None,
        support_graph: SupportFlowGraph | None = None,
        faq_graph: FAQFlowGraph | None = None,
    ) -> None:
        self.task_queue_service = task_queue_service
        self.task_planner = task_planner
        self.context_manager = context_manager
        self.whatsapp_client = whatsapp_client
        self.flow_context_service = flow_context_service or FlowContextService()

        # Build handler registry (order matters - first match wins)
        self._handlers: list[IntentHandler] = [
            TransferHandler(transfer_service),
            AirtimeHandler(airtime_service),
            DataHandler(data_graph),
            QueryHandler(query_graph, support_graph),
            SupportHandler(support_graph, conversation_responder),
            FAQHandler(faq_graph, support_graph, conversation_responder),
            AccountsHandler(account_management_service, query_graph),
            ConversationalHandler(conversation_responder, query_graph),  # Fallback
        ]

    def _get_handler(self, intent: str) -> IntentHandler | None:
        """Find handler for the given intent."""
        for handler in self._handlers:
            if handler.can_handle(intent):
                return handler
        return None

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

        is_multiple = result.is_complex and "multiple" in result.complexity_reason.lower()
        if ctx.intent == "mixed" or is_multiple:
            return await self._handle_multi_task(ctx)

        handler = self._get_handler(ctx.intent)
        if not handler:
            logger.warning("no_handler_found", intent=ctx.intent)
            handler = self._handlers[-1]  # Fallback to conversational

        # Clear flow state for cancel/reset intents
        if result.is_cancellation or result.intent == "cancel":
            await self.context_manager.clear_conversation_state(phone_number)
            logger.info("cleared_conversation_state_on_cancel", phone=phone_number[:6])

        await self._pause_if_needed(ctx, handler.pausable_flows)

        if handler.send_ack_before_handling and ctx.result.response:
            await self._send_ack(ctx)

        response = await handler.handle(ctx)

        if handler.supports_resume_prompt:
            response = await self._append_resume_prompt(response, phone_number)

        return response
