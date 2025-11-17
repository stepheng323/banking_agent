"""Intent-based routing for the orchestrator."""

from typing import Any
import asyncio
import traceback

from apps.core.src.agent.models.classification import ClassificationResult
from apps.core.src.agent.services.task_queue_service import TaskQueueService
from apps.core.src.agent.services.conversation_responder import ConversationResponder
from apps.core.src.agent.transfer import TransferService
from apps.core.src.agent.airtime import AirtimeService
from apps.core.src.agent.orchestrator.task_planner import OrchestratorTaskPlanner
from apps.core.src.agent.orchestrator.context_manager import OrchestratorContextManager


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
    ) -> None:
        self.task_queue_service = task_queue_service
        self.task_planner = task_planner
        self.transfer_service = transfer_service
        self.airtime_service = airtime_service
        self.conversation_responder = conversation_responder
        self.context_manager = context_manager

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
        if intent == "mixed" or (result.is_complex and len(result.intent.split()) > 1):
            try:
                planner_output = await self.task_planner.plan_tasks(phone_number, text)
                if planner_output.tasks and len(planner_output.tasks) > 1:
                    await self.task_queue_service.create_task_queue(
                        phone_number, planner_output
                    )
                    next_task_response = await self.task_planner.handle_next_task(phone_number, text)
                    if next_task_response:
                        asyncio.create_task(
                            self.context_manager.save_last_response(
                                phone_number, next_task_response)
                        )
                        return next_task_response
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
