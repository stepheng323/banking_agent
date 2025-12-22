"""Intent-based routing for the orchestrator."""

import json
from typing import Any, Optional
import asyncio
import traceback

from apps.core.src.agent.orchestrator.models.classification import ClassificationResult
from shared.types.planner import PlannerOutput
from shared.services.task_queue import TaskQueueService
from apps.core.src.agent.orchestrator.services.conversation_responder import ConversationResponder
from apps.core.src.agent.sub_agents.transfer import TransferService
from apps.core.src.agent.sub_agents.airtime import AirtimeService
from apps.core.src.agent.sub_agents.query.graph import QueryFlowGraph
from apps.core.src.agent.sub_agents.account_management.service import AccountManagementService
from apps.core.src.agent.orchestrator.features.task_planning.service import OrchestratorTaskPlanner
from apps.core.src.agent.orchestrator.features.context.service import OrchestratorContextManager
from apps.core.src.agent.orchestrator.features.flow_context.service import FlowContextService
from shared.clients.whatsapp.client import WhatsAppClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class OrchestratorIntentRouter:
    """Routes intents to appropriate services."""

    def __init__(
        self,
        task_queue_service: TaskQueueService,
        task_planner: OrchestratorTaskPlanner,
        transfer_service: TransferService,
        airtime_service: AirtimeService,
        conversation_responder: ConversationResponder,
        context_manager: OrchestratorContextManager,
        query_graph: QueryFlowGraph,
        account_management_service: AccountManagementService,
        whatsapp_client: WhatsAppClient,
        flow_context_service: Optional[FlowContextService] = None,
    ) -> None:
        self.task_queue_service = task_queue_service
        self.task_planner = task_planner
        self.transfer_service = transfer_service
        self.airtime_service = airtime_service
        self.conversation_responder = conversation_responder
        self.context_manager = context_manager
        self.whatsapp_client = whatsapp_client
        self.query_graph = query_graph
        self.account_management_service = account_management_service
        self.flow_context_service = flow_context_service or FlowContextService()

    def _generate_task_acknowledgment(
        self, planner_output: PlannerOutput
    ) -> str:
        """
        Generate friendly acknowledgment message for multi-task requests.
        
        Args:
            planner_output: Planner output with tasks
            
        Returns:
            Acknowledgment message string
        """
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
                return f"I'll help you {normalized.lower()}. I'll process these one at a time. Let's start with the transfer to {recipient_name}."
            else:
                return f"I'll help you {normalized.lower()}. I'll process these one at a time. Let's start with the first {first_task_type}."
        else:
            return f"I'll help you {normalized.lower()}. Let's get started."

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
        intent = result.intent.lower()

        is_multiple_transactions = (
            result.is_complex and 
            "multiple" in result.complexity_reason.lower()
        )
        
        if intent == "mixed" or is_multiple_transactions:
            try:
                planner_output = await self.task_planner.plan_tasks(phone_number, text)
                logger.info("log_event")
                if planner_output.tasks:
                    await self.task_queue_service.create_task_queue(
                        phone_number, planner_output
                    )
                    
                    acknowledgment = self._generate_task_acknowledgment(planner_output)
                    logger.debug("generated")
                    
                    # Send acknowledgment directly
                    await self.whatsapp_client.send_text(
                        phone_number, acknowledgment, message_id=message_id
                    )
                    
                    asyncio.create_task(
                        self.context_manager.save_last_response(
                            phone_number, acknowledgment)
                    )
                    
                    next_task_response = await self.task_planner.handle_next_task(phone_number, text)
                    if next_task_response and next_task_response.strip():
                        # Send task prompt separately if it's different from acknowledgment
                        await self.whatsapp_client.send_text(
                            phone_number, next_task_response, message_id=message_id
                        )
                        asyncio.create_task(
                            self.context_manager.save_last_response(
                                phone_number, next_task_response)
                        )
                    # Return empty to prevent message_consumer from sending duplicate
                    return ""
            except Exception as e:
                logger.error("error_in")
                traceback.print_exc()

        if intent == "transfer":
            # Check if airtime is active - if so, pause it first
            conversation_state = await self.context_manager.get_conversation_state(phone_number)
            if conversation_state and conversation_state.get("active_flow") == "airtime":
                flow_summary = {
                    "amount": conversation_state.get("amount"),
                    "recipient_phone": conversation_state.get("recipient_phone"),
                }
                await self.flow_context_service.pause_flow(
                    phone_number, "airtime", "transfer", flow_summary
                )
                
            transfer_classification_dict = result.model_dump() if hasattr(result, 'model_dump') else {
                "intent": result.intent,
                "is_cancellation": result.is_cancellation,
                "confidence": result.confidence,
            }
            response = await self.transfer_service.run_simple(
                phone_number, text, transfer_classification_dict, image_data=image_data
            )
        elif intent == "airtime":
            # Check if transfer is active - if so, pause it first
            conversation_state = await self.context_manager.get_conversation_state(phone_number)
            if conversation_state and conversation_state.get("active_flow") == "transfer":
                flow_summary = {
                    "amount": conversation_state.get("amount"),
                    "recipient_name": conversation_state.get("recipient_name"),
                    "recipient_account": conversation_state.get("recipient_account"),
                }
                await self.flow_context_service.pause_flow(
                    phone_number, "transfer", "airtime", flow_summary
                )
                
            airtime_classification_dict = result.model_dump() if hasattr(result, 'model_dump') else {
                "intent": result.intent,
                "is_cancellation": result.is_cancellation,
                "confidence": result.confidence,
            }
            response = await self.airtime_service.run_simple(phone_number, text, airtime_classification_dict)
        elif intent == "data":
            response = "Data purchase flow coming soon."

        elif intent == "query":
            # Check if there's an active flow to pause
            conversation_state = await self.context_manager.get_conversation_state(phone_number)
            if conversation_state:
                active_flow = conversation_state.get("active_flow")
                if active_flow in ("transfer", "airtime"):
                    # Pause the flow before handling query
                    flow_summary = {
                        "amount": conversation_state.get("amount"),
                        "recipient_name": conversation_state.get("recipient_name"),
                        "recipient_phone": conversation_state.get("recipient_phone"),
                    }
                    await self.flow_context_service.pause_flow(
                        phone_number, active_flow, "balance_query", flow_summary
                    )
            
            response = await self.query_graph.run(phone_number, text, user_ctx)
            
            # Check if we need to append resume prompt
            resume_prompt = await self.flow_context_service.generate_resume_prompt(phone_number)
            if resume_prompt:
                response = f"{response}\n\n{resume_prompt}"

        elif intent == "manage_accounts":
            # Check if there's an active flow to pause
            conversation_state = await self.context_manager.get_conversation_state(phone_number)
            if conversation_state:
                active_flow = conversation_state.get("active_flow")
                if active_flow in ("transfer", "airtime"):
                    flow_summary = {
                        "amount": conversation_state.get("amount"),
                        "recipient_name": conversation_state.get("recipient_name"),
                        "recipient_phone": conversation_state.get("recipient_phone"),
                    }
                    await self.flow_context_service.pause_flow(
                        phone_number, active_flow, "account_management", flow_summary
                    )
            
            response = await self.account_management_service.handle_account_management(phone_number, text, user_ctx)
            
            # Check if we need to append resume prompt
            resume_prompt = await self.flow_context_service.generate_resume_prompt(phone_number)
            if resume_prompt:
                response = f"{response}\n\n{resume_prompt}"
                
        elif intent == "conversational":

            conv = await self.conversation_responder.generate_reply(phone_number, text, result, user_ctx)
            logger.info("conversation")
            response = conv
        else:
            conv = await self.conversation_responder.generate_reply(phone_number, text, result, user_ctx)
            response = conv

        return response
