"""Intent-based routing for the orchestrator."""

import json
from typing import Any
import asyncio
import traceback

from apps.core.src.agent.orchestrator.models.classification import ClassificationResult
from apps.core.src.agent.orchestrator.models.planner import PlannerOutput
from apps.core.src.agent.orchestrator.services.task_queue_service import TaskQueueService
from apps.core.src.agent.orchestrator.services.conversation_responder import ConversationResponder
from apps.core.src.agent.sub_agents.transfer import TransferService
from apps.core.src.agent.sub_agents.airtime import AirtimeService
from apps.core.src.agent.sub_agents.query.service import QueryService
from apps.core.src.agent.sub_agents.account_management.service import AccountManagementService
from apps.core.src.agent.orchestrator.features.task_planning.service import OrchestratorTaskPlanner
from apps.core.src.agent.orchestrator.features.context.service import OrchestratorContextManager
from shared.clients.whatsapp_client import WhatsAppClient


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
        query_service: QueryService,
        account_management_service: AccountManagementService,
        whatsapp_client: WhatsAppClient,
    ) -> None:
        self.task_queue_service = task_queue_service
        self.task_planner = task_planner
        self.transfer_service = transfer_service
        self.airtime_service = airtime_service
        self.conversation_responder = conversation_responder
        self.context_manager = context_manager
        self.whatsapp_client = whatsapp_client
        self.query_service = query_service
        self.account_management_service = account_management_service

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
        
        # Determine task type and extract recipient name from first task
        first_task_type = "task"
        recipient_name = None
        if planner_output.tasks:
            first_task = planner_output.tasks[0]
            executor = first_task.executor
            if executor == "transfer":
                first_task_type = "transfer"
                # Extract recipient name from task parameters
                params = first_task.parameters or {}
                recipient_name = params.get("recipient")
            elif executor == "airtime":
                first_task_type = "airtime purchase"
            elif executor == "query":
                first_task_type = "query"
        
        if task_count > 1:
            # Use recipient name if available, otherwise fall back to generic
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
    ) -> str:
        """
        Route intent to appropriate service.

        Args:
            phone_number: User's phone number
            text: User's message
            result: Classification result
            user_ctx: User context

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
                print(f"planner_output: {json.dumps(planner_output.model_dump(), indent=4)}")
                if planner_output.tasks:
                    await self.task_queue_service.create_task_queue(
                        phone_number, planner_output
                    )
                    
                    acknowledgment = self._generate_task_acknowledgment(planner_output)
                    print(f"🔍 [INTENT_ROUTER] Generated acknowledgment: {acknowledgment}")
                    
                    await self.whatsapp_client.send_text(
                        phone_number, acknowledgment
                    )
                    
                    asyncio.create_task(
                        self.context_manager.save_last_response(
                            phone_number, acknowledgment)
                    )
                    
                    next_task_response = await self.task_planner.handle_next_task(phone_number, text)
                    if next_task_response:
                        asyncio.create_task(
                            self.context_manager.save_last_response(
                                phone_number, next_task_response)
                        )
                        return next_task_response
                    else:
                        return acknowledgment
            except Exception as e:
                print(f"⚠️  Error in multi-task planning: {e}")
                traceback.print_exc()

        if intent == "transfer":
            transfer_classification_dict = result.model_dump() if hasattr(result, 'model_dump') else {
                "intent": result.intent,
                "is_cancellation": result.is_cancellation,
                "confidence": result.confidence,
            }
            response = await self.transfer_service.run_simple(phone_number, text, transfer_classification_dict)
        elif intent == "airtime":
            airtime_classification_dict = result.model_dump() if hasattr(result, 'model_dump') else {
                "intent": result.intent,
                "is_cancellation": result.is_cancellation,
                "confidence": result.confidence,
            }
            response = await self.airtime_service.run_simple(phone_number, text, airtime_classification_dict)
        elif intent == "data":
            response = "Data purchase flow coming soon."

        elif intent == "query":
            response = await self.query_service.handle_query(text, user_ctx)

        elif intent == "manage_accounts":
            response = await self.account_management_service.handle_account_management(phone_number, text, user_ctx)
        elif intent == "conversational":

            conv = await self.conversation_responder.generate_reply(phone_number, text, result, user_ctx)
            print(f"Conversation response: {conv}")
            response = conv
        else:
            conv = await self.conversation_responder.generate_reply(phone_number, text, result, user_ctx)
            response = conv

        return response
