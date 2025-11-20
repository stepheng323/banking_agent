"""Minimal orchestrator: LLM-based multilingual intent+complexity and user context cache."""

import json
import asyncio

from langchain_openai import ChatOpenAI

from shared.cache import UserContextCacheService
from shared.clients.whatsapp_client import WhatsAppClient
from shared.repositories import UserRepository
from shared.cache.redis_client import RedisClient

from apps.core.src.agent.models import ClassificationResult, PlannerOutput
from apps.core.src.agent.orchestrator.flow_completion_callback import (
    OrchestratorFlowCompletionCallback)
from apps.core.src.agent.services import ConversationResponder, TaskQueueService, TaskExecutor
from apps.core.src.agent.transfer import TransferService
from apps.core.src.agent.airtime import AirtimeService

from apps.core.src.agent.orchestrator.context_manager import OrchestratorContextManager
from apps.core.src.agent.orchestrator.classification_service import OrchestratorClassificationService
from apps.core.src.agent.orchestrator.task_planner import OrchestratorTaskPlanner
from apps.core.src.agent.orchestrator.beneficiary_handler import OrchestratorBeneficiaryHandler
from apps.core.src.agent.orchestrator.cancellation_handler import OrchestratorCancellationHandler
from apps.core.src.agent.orchestrator.intent_router import OrchestratorIntentRouter


class OrchestratorAgent:
    """Orchestrator agent for the banking assistant."""

    def __init__(
        self,
        llm: ChatOpenAI,
        user_repo: UserRepository,
        user_cache: UserContextCacheService,
        whatsapp_client: WhatsAppClient,
        task_queue_service: TaskQueueService,
        conversation_responder: ConversationResponder,
        transfer_service: TransferService,
        airtime_service: AirtimeService,
        task_executor: TaskExecutor,
    ) -> None:
        self.llm = llm
        self.classifier_llm = self.llm.with_structured_output(
            ClassificationResult)
        self.planner_llm = self.llm.with_structured_output(PlannerOutput)
        self.user_repo = user_repo
        self.user_cache = user_cache
        self.whatsapp_client = whatsapp_client
        self.conversation = conversation_responder
        self.task_queue_service = task_queue_service
        self.transfer = transfer_service
        self.airtime = airtime_service
        self.task_executor = task_executor

        # Create completion callback (needs reference to self)
        self.completion_callback = OrchestratorFlowCompletionCallback(
            self.task_queue_service, self
        )

        # Initialize extracted modules with dependency injection
        self.context_manager = OrchestratorContextManager(
            user_cache=self.user_cache,
            user_repo=self.user_repo,
        )
        self.classification_service = OrchestratorClassificationService(
            classifier_llm=self.classifier_llm,
        )
        self.task_planner = OrchestratorTaskPlanner(
            planner_llm=self.planner_llm,
            task_queue_service=self.task_queue_service,
            task_executor=self.task_executor,
        )
        self.beneficiary_handler = OrchestratorBeneficiaryHandler(
            context_manager=self.context_manager,
        )
        self.cancellation_handler = OrchestratorCancellationHandler(
            transfer_service=self.transfer,
            airtime_service=self.airtime,
            context_manager=self.context_manager,
        )
        self.intent_router = OrchestratorIntentRouter(
            task_queue_service=self.task_queue_service,
            task_planner=self.task_planner,
            transfer_service=self.transfer,
            airtime_service=self.airtime,
            conversation_responder=self.conversation,
            context_manager=self.context_manager,
        )

    async def invoke(self, phone_number: str, text: str, _message_id: str) -> str:
        """Invoke the orchestrator agent."""
        if not text or not text.strip():
            return "Please send a message with your request."

        user_ctx = await self.context_manager.load_user_context(phone_number)

        has_active_queue = await self.task_queue_service.has_active_queue(phone_number)
        if has_active_queue:
            current_task_id = await self.task_queue_service.get_current_task(phone_number)
            if current_task_id:
                planner_output = await self.task_queue_service.get_task_queue(phone_number)
                if planner_output:
                    for task in planner_output.tasks:
                        if task.id == current_task_id:
                            if task.executor == "transfer":
                                task_classification_dict = {
                                    "intent": "transfer"}
                                task_response = await self.transfer.run_simple(phone_number, text, task_classification_dict)
                                asyncio.create_task(
                                    self.context_manager.save_last_response(phone_number, task_response))
                                return task_response
                            elif task.executor == "airtime":
                                task_classification_dict = {
                                    "intent": "airtime"}
                                task_response = await self.airtime.run_simple(phone_number, text, task_classification_dict)
                                asyncio.create_task(
                                    self.context_manager.save_last_response(phone_number, task_response))
                                return task_response
                            break
            else:
                next_task_response = await self.task_planner.handle_next_task(phone_number, text)
                if next_task_response:
                    asyncio.create_task(
                        self.context_manager.save_last_response(
                            phone_number, next_task_response)
                    )
                    return next_task_response

        conversation_state = await self.context_manager.get_conversation_state(phone_number)
        last_response = await self.context_manager.get_last_response(phone_number)

        redis_client = RedisClient.get_client()
        suggestion_key = f"user:{phone_number}:beneficiary_suggestion"
        suggestion_data = await redis_client.get(suggestion_key)
        suggestion_context = None

        classification_context = {}
        if conversation_state:
            classification_context["conversationState"] = conversation_state

        if suggestion_data:
            suggestion_context = json.loads(suggestion_data)
            classification_context["pendingBeneficiarySuggestion"] = suggestion_context
            # Don't override last_response - use the actual last response sent to the user
            # This ensures the classifier sees the actual prompt (e.g., "Please provide a name or alias...")
            # rather than a hardcoded message

        result = await self.classification_service.classify(
            text,
            classification_context if classification_context else None,
            last_response,
        )


        asyncio.create_task(
            self.context_manager.save_classification_result(phone_number, result))

        intent = result.intent.lower()

        # Transaction intents that should NOT be treated as beneficiary responses
        transaction_intents = {"transfer", "airtime", "data"}
        
        if suggestion_context:
            # If intent is a transaction intent, clear stale suggestion key and skip beneficiary handling
            if intent in transaction_intents:
                await redis_client.delete(suggestion_key)
                suggestion_context = None
            else:
                # Only handle beneficiary response if intent is NOT a transaction intent
                response = await self.beneficiary_handler.handle_beneficiary_response(
                    phone_number, text, result, suggestion_context
                )
                if response:
                    return response

        is_cancellation = (
            intent == "cancel" or result.is_cancellation is True)
        if is_cancellation:
            response = await self.cancellation_handler.handle_cancellation(
                phone_number, text, result, conversation_state
            )
            if response:
                return response

        response = await self.intent_router.route_intent(
            phone_number, text, result, user_ctx
        )
        asyncio.create_task(
            self.context_manager.save_last_response(phone_number, response))

        return response
