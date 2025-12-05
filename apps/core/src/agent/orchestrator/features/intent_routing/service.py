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
from apps.core.src.agent.orchestrator.features.task_planning.service import OrchestratorTaskPlanner
from apps.core.src.agent.orchestrator.features.context.service import OrchestratorContextManager


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
        orchestrator: Any = None,  # Optional orchestrator reference for sending messages
    ) -> None:
        self.task_queue_service = task_queue_service
        self.task_planner = task_planner
        self.transfer_service = transfer_service
        self.airtime_service = airtime_service
        self.conversation_responder = conversation_responder
        self.context_manager = context_manager
        self.orchestrator = orchestrator

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

        # Handle mixed/complex intents with task planning
        # Check if complex and complexity_reason indicates multiple operations
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
                    
                    # Generate and send acknowledgment message separately BEFORE executing task
                    acknowledgment = self._generate_task_acknowledgment(planner_output)
                    print(f"🔍 [INTENT_ROUTER] Generated acknowledgment: {acknowledgment}")
                    
                    # Send acknowledgment via WhatsApp client - use orchestrator's whatsapp_client directly
                    try:
                        if self.orchestrator and hasattr(self.orchestrator, 'whatsapp_client'):
                            await self.orchestrator.whatsapp_client.send_text(
                                phone_number, acknowledgment
                            )
                            print(f"✅ [INTENT_ROUTER] Sent acknowledgment message")
                        else:
                            print(f"⚠️  [INTENT_ROUTER] Orchestrator or whatsapp_client not available, cannot send acknowledgment")
                    except Exception as e:
                        print(f"❌ [INTENT_ROUTER] Error sending acknowledgment: {e}")
                        traceback.print_exc()
                    
                    # Save acknowledgment as last response
                    asyncio.create_task(
                        self.context_manager.save_last_response(
                            phone_number, acknowledgment)
                    )
                    
                    # Execute first task (this will return the task's initial response)
                    next_task_response = await self.task_planner.handle_next_task(phone_number, text)
                    if next_task_response:
                        # Save task response separately
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

        # Route to specific services
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
        elif intent == "conversational":
            conv = await self.conversation_responder.generate_reply(phone_number, text, result, user_ctx)
            print(f"Conversation response: {conv}")
            response = conv
        else:
            conv = await self.conversation_responder.generate_reply(phone_number, text, result, user_ctx)
            response = conv

        return response
