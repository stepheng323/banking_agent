"""Minimal orchestrator: LLM-based multilingual intent+complexity and user context cache."""

from typing import Any

from langchain_openai import ChatOpenAI

from apps.core.src.agent.orchestrator.config import OrchestratorDependencies

from apps.core.src.agent.orchestrator.handlers.affirmation import AffirmationHandler
from apps.core.src.agent.orchestrator.handlers.batch_auth import (
    BatchAuthorizationHandler,
)
from apps.core.src.agent.orchestrator.handlers.beneficiary import BeneficiaryHandler
from apps.core.src.agent.orchestrator.services.beneficiary_service import (
    OrchestratorBeneficiaryHandler,
)
from apps.core.src.agent.orchestrator.services.cancellation_service import (
    OrchestratorCancellationHandler,
)
from apps.core.src.agent.orchestrator.handlers.flow_control import FlowControlHandler
from apps.core.src.agent.orchestrator.handlers.classification import ClassificationHandler
from apps.core.src.agent.orchestrator.services.classifier import (
    OrchestratorClassificationService,
)
from apps.core.src.agent.orchestrator.handlers.context_loader import ContextLoaderHandler
from apps.core.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from apps.core.src.agent.orchestrator.services.flow_manager import FlowContextService
from apps.core.src.agent.orchestrator.handlers.task_queue import TaskQueueHandler
from apps.core.src.agent.orchestrator.handlers.intent_routing import IntentRoutingHandler
from apps.core.src.agent.orchestrator.services.intent_router import (
    OrchestratorIntentRouter,
)
from apps.core.src.agent.orchestrator.handlers.quote import QuoteHandler
from apps.core.src.agent.orchestrator.services.quote_service import QuoteService

from apps.core.src.agent.orchestrator.services.planner import OrchestratorTaskPlanner
from apps.core.src.agent.orchestrator.services.task_coordinator import TaskCoordinator
from apps.core.src.agent.orchestrator.pipeline import MessageContext, MessagePipeline
from apps.core.src.agent.orchestrator.services import (
    ConversationResponder,
    TaskExecutor,
    TaskQueueService,
)
from apps.core.src.agent.sub_agents.account_management.service import AccountManagementService
from apps.core.src.agent.sub_agents.airtime import AirtimeService
from apps.core.src.agent.sub_agents.data import DataPurchaseGraph
from apps.core.src.agent.sub_agents.faq import FAQFlowGraph
from apps.core.src.agent.sub_agents.query.graph import QueryFlowGraph
from apps.core.src.agent.sub_agents.support.graph import SupportFlowGraph
from apps.core.src.agent.sub_agents.transfer import TransferService
from shared.clients.whatsapp.client import WhatsAppClient
from shared.repositories import BeneficiaryRepository, UserRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.utils.async_helpers import create_background_task


class OrchestratorAgent:
    """Orchestrator agent for the banking assistant."""

    def __init__(self, deps: OrchestratorDependencies) -> None:
        self.deps = deps
        self.llm = deps.llm
        self.user_repo = deps.user_repo
        self.whatsapp_client = deps.whatsapp_client
        self.task_queue_service = deps.task_queue_service
        self.conversation_responder = deps.conversation_responder
        self.transfer_service = deps.transfer_service
        self.airtime_service = deps.airtime_service
        self.task_executor = deps.task_executor
        self.query_graph = deps.query_graph
        self.account_management_service = deps.account_management_service
        self.media_service = deps.media_service
        self.data_graph = deps.data_graph
        self.support_graph = deps.support_graph
        self.faq_graph = deps.faq_graph

        self.context_manager = OrchestratorContextManager(deps.user_repo, deps.beneficiary_repo)
        self.classification_service = OrchestratorClassificationService(deps.llm)
        self.task_planner = OrchestratorTaskPlanner(deps.llm, deps.task_queue_service, deps.task_executor)
        self.beneficiary_handler = OrchestratorBeneficiaryHandler(self.context_manager)
        self.completion_callback = TaskCoordinator(
            deps.task_queue_service,
            deps.whatsapp_client,
            self.context_manager,
            self.task_planner,
            deps.transfer_service,
        )
        self.cancellation_handler = OrchestratorCancellationHandler(
            deps.transfer_service, deps.airtime_service, self.context_manager, deps.task_queue_service
        )
        self.flow_context_service = FlowContextService()

        
        from apps.core.src.agent.orchestrator.services.intent_router_deps import IntentRouterDependencies
        router_deps = IntentRouterDependencies(
            task_queue_service=deps.task_queue_service,
            task_planner=self.task_planner,
            transfer_service=deps.transfer_service,
            airtime_service=deps.airtime_service,
            conversation_responder=deps.conversation_responder,
            context_manager=self.context_manager,
            query_graph=deps.query_graph,
            whatsapp_client=self.whatsapp_client,
            flow_context_service=self.flow_context_service,
            data_graph=deps.data_graph,
            account_management_service=deps.account_management_service,
            support_graph=deps.support_graph,
            faq_graph=deps.faq_graph,
        )
        self.intent_router = OrchestratorIntentRouter(router_deps)
        self._handlers = [
            ContextLoaderHandler(self.context_manager, deps.task_queue_service),
            ClassificationHandler(self.classification_service, self.context_manager, deps.actionable_message_repo),
            FlowControlHandler(self.context_manager, self.cancellation_handler, deps.transfer_service, deps.airtime_service),
            AffirmationHandler(deps.transfer_service, deps.airtime_service, self.flow_context_service, deps.llm),
            QuoteHandler(
                QuoteService(deps.executor_registry),
                self.whatsapp_client,
            ),
            BeneficiaryHandler(self.beneficiary_handler),
            BatchAuthorizationHandler(deps.task_queue_service, deps.transfer_service, deps.whatsapp_client),
            TaskQueueHandler(deps.task_queue_service, self.task_planner, deps.transfer_service, deps.airtime_service, deps.executor_registry),
            IntentRoutingHandler(self.intent_router),
        ]

    @property
    def transfer(self) -> TransferService:
        """Get transfer service."""
        return self.transfer_service

    async def invoke(
        self,
        phone_number: str,
        text: str,
        message_id: str,
        message_type: str = "text",
        media_id: str | None = None,
        quoted_message_id: str | None = None,
    ) -> str:
        """Invoke the orchestrator with a user message using the pipeline."""
        self.message_type = message_type

        if self.message_type == "audio" and media_id:
            text = await self.media_service.process_audio(media_id)

        image_data = None
        if self.message_type == "image" and media_id:
            image_data = await self.media_service.get_image_data(media_id)

        initial_context = MessageContext(
            phone_number=phone_number,
            text=text,
            message_id=message_id,
            image_data=image_data,
            quoted_message_id=quoted_message_id,
        )

        pipeline = MessagePipeline(self._handlers)
        response = await pipeline.process(initial_context)

        create_background_task(self.context_manager.add_conversation_turn(phone_number, "user", text))
        create_background_task(self.context_manager.add_conversation_turn(phone_number, "assistant", response))

        return response
