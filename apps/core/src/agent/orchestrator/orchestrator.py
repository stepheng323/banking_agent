"""Minimal orchestrator: LLM-based multilingual intent+complexity and user context cache."""

import json
import asyncio
from typing import Any

from langchain_openai import ChatOpenAI

from shared.clients.whatsapp_client import WhatsAppClient
from shared.clients.whatsapp_client import WhatsAppClient
from shared.repositories import UserRepository, BeneficiaryRepository
from shared.cache.redis_client import RedisClient
from shared.cache.redis_client import RedisClient

from apps.core.src.agent.models import ClassificationResult, PlannerOutput
from apps.core.src.agent.orchestrator.flow_completion_callback import (
    OrchestratorFlowCompletionCallback)
from apps.core.src.agent.orchestrator.services import ConversationResponder, TaskQueueService, TaskExecutor
from apps.core.src.agent.transfer import TransferService
from apps.core.src.agent.airtime import AirtimeService
from shared.types.agent_types import TaskStatus

from apps.core.src.agent.orchestrator.features.context.service import OrchestratorContextManager
from apps.core.src.agent.orchestrator.features.classification.service import OrchestratorClassificationService
from apps.core.src.agent.orchestrator.features.task_planning.service import OrchestratorTaskPlanner
from apps.core.src.agent.orchestrator.features.beneficiary.service import OrchestratorBeneficiaryHandler
from apps.core.src.agent.orchestrator.features.cancellation.service import OrchestratorCancellationHandler
from apps.core.src.agent.orchestrator.features.intent_routing.service import OrchestratorIntentRouter
from apps.core.src.agent.query.service import QueryService
from apps.core.src.agent.account_management.service import AccountManagementService

from apps.core.src.agent.orchestrator.pipeline import MessageContext, MessagePipeline
from apps.core.src.agent.orchestrator.features.context.handler import ContextLoaderHandler
from apps.core.src.agent.orchestrator.features.classification.handler import ClassificationHandler
from apps.core.src.agent.orchestrator.features.fresh_start.handler import FreshStartHandler
from apps.core.src.agent.orchestrator.features.beneficiary.handler import BeneficiaryHandler
from apps.core.src.agent.orchestrator.features.cancellation.handler import CancellationHandler
from apps.core.src.agent.orchestrator.features.batch_authorization.handler import BatchAuthorizationHandler
from apps.core.src.agent.orchestrator.features.active_queue.handler import ActiveQueueHandler
from apps.core.src.agent.orchestrator.features.task_planning.handler import NextTaskHandler
from apps.core.src.agent.orchestrator.features.intent_routing.handler import IntentRoutingHandler
from apps.core.src.agent.orchestrator.features.query.handler import QueryHandler
from apps.core.src.agent.orchestrator.features.account_management.handler import AccountManagementHandler


class OrchestratorAgent:
    """Orchestrator agent for the banking assistant."""

    def __init__(
        self,
        llm: ChatOpenAI,
        user_repo: UserRepository,
        beneficiary_repo: BeneficiaryRepository,
        whatsapp_client: WhatsAppClient,
        task_queue_service: TaskQueueService,
        conversation_responder: ConversationResponder,
        transfer_service: TransferService,
        airtime_service: AirtimeService,
        task_executor: TaskExecutor,
        query_service: QueryService,
        account_management_service: AccountManagementService
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
            user_repo,
            beneficiary_repo
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
        # Handler order matters
        self._handlers = [
            ContextLoaderHandler(self.context_manager, task_queue_service),
            ClassificationHandler(self.classification_service, self.context_manager),
            FreshStartHandler(self.context_manager, transfer_service),
            BeneficiaryHandler(self.beneficiary_handler),
            CancellationHandler(self.cancellation_handler),
            BatchAuthorizationHandler(task_queue_service, transfer_service),
            ActiveQueueHandler(task_queue_service, transfer_service, airtime_service),
            NextTaskHandler(self.task_planner),
            AccountManagementHandler(account_management_service),
            QueryHandler(query_service),
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
