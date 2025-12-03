"""Minimal orchestrator: LLM-based multilingual intent+complexity and user context cache."""

import json
import asyncio
from typing import Any

from langchain_openai import ChatOpenAI

from shared.clients.whatsapp_client import WhatsAppClient
from shared.repositories import UserRepository
from shared.cache.redis_client import RedisClient

from apps.core.src.agent.models import ClassificationResult, PlannerOutput
from apps.core.src.agent.orchestrator.flow_completion_callback import (
    OrchestratorFlowCompletionCallback)
from apps.core.src.agent.services import ConversationResponder, TaskQueueService, TaskExecutor
from apps.core.src.agent.transfer import TransferService
from apps.core.src.agent.airtime import AirtimeService
from shared.types.agent_types import TaskStatus

from apps.core.src.agent.orchestrator.context_manager import OrchestratorContextManager
from apps.core.src.agent.orchestrator.classification_service import OrchestratorClassificationService
from apps.core.src.agent.orchestrator.task_planner import OrchestratorTaskPlanner
from apps.core.src.agent.orchestrator.beneficiary_handler import OrchestratorBeneficiaryHandler
from apps.core.src.agent.orchestrator.cancellation_handler import OrchestratorCancellationHandler
from apps.core.src.agent.orchestrator.intent_router import OrchestratorIntentRouter

from apps.core.src.agent.orchestrator.pipeline import MessageContext, MessagePipeline
from apps.core.src.agent.orchestrator.pipeline.handlers import (
    ContextLoaderHandler,
    ClassificationHandler,
    FreshStartHandler,
    BeneficiaryHandler,
    CancellationHandler,
    BatchAuthorizationHandler,
    ActiveQueueHandler,
    NextTaskHandler,
    IntentRoutingHandler
)


class OrchestratorAgent:
    """Orchestrator agent for the banking assistant."""

    def __init__(
        self,
        llm: ChatOpenAI,
        user_repo: UserRepository,
        whatsapp_client: WhatsAppClient,
        task_queue_service: TaskQueueService,
        conversation_responder: ConversationResponder,
        transfer_service: TransferService,
        airtime_service: AirtimeService,
        task_executor: TaskExecutor,
    ) -> None:
        self.llm = llm
        self.user_repo = user_repo
        self.whatsapp_client = whatsapp_client
        self.task_queue_service = task_queue_service
        self.conversation_responder = conversation_responder
        self.transfer_service = transfer_service
        self.airtime_service = airtime_service
        self.task_executor = task_executor

        self.context_manager = OrchestratorContextManager(
            user_repo
        )
        self.classification_service = OrchestratorClassificationService(llm)
        self.task_planner = OrchestratorTaskPlanner(llm, task_queue_service, task_executor)
        self.beneficiary_handler = OrchestratorBeneficiaryHandler(
            self.context_manager
        )
        self.completion_callback = OrchestratorFlowCompletionCallback(
            task_queue_service, self
        )
        self.cancellation_handler = OrchestratorCancellationHandler(
            transfer_service, airtime_service, self.context_manager, task_queue_service
        )
        self.intent_router = OrchestratorIntentRouter(
            task_queue_service,
            self.task_planner,
            transfer_service,
            airtime_service,
            conversation_responder,
            self.context_manager,
            self,
        )
        
        self._handlers = [
            ContextLoaderHandler(self.context_manager, task_queue_service),
            ClassificationHandler(self.classification_service, self.context_manager),
            FreshStartHandler(self.context_manager, transfer_service),
            BeneficiaryHandler(self.beneficiary_handler),
            CancellationHandler(self.cancellation_handler),
            BatchAuthorizationHandler(task_queue_service, transfer_service),
            ActiveQueueHandler(task_queue_service, transfer_service, airtime_service),
            NextTaskHandler(self.task_planner),
            IntentRoutingHandler(self.intent_router),
        ]

    @property
    def transfer(self) -> TransferService:
        """Get transfer service."""
        return self.transfer_service

    async def invoke(self, phone_number: str, text: str, message_id: str) -> str:
        """Invoke the orchestrator with a user message using the pipeline."""
        
        initial_context = MessageContext(
            phone_number=phone_number,
            text=text,
            message_id=message_id
        )
        
        pipeline = MessagePipeline(self._handlers)
        response = await pipeline.process(initial_context)
        
        return response
