"""Minimal orchestrator: LLM-based multilingual intent+complexity and user context cache."""

from typing import Any

from langchain_openai import ChatOpenAI

from apps.core.src.agent.orchestrator.features.active_queue.handler import ActiveQueueHandler
from apps.core.src.agent.orchestrator.features.affirmation import AffirmationHandler
from apps.core.src.agent.orchestrator.features.batch_authorization.handler import (
    BatchAuthorizationHandler,
)
from apps.core.src.agent.orchestrator.features.beneficiary.handler import BeneficiaryHandler
from apps.core.src.agent.orchestrator.features.beneficiary.service import (
    OrchestratorBeneficiaryHandler,
)
from apps.core.src.agent.orchestrator.features.cancellation.handler import CancellationHandler
from apps.core.src.agent.orchestrator.features.cancellation.service import (
    OrchestratorCancellationHandler,
)
from apps.core.src.agent.orchestrator.features.classification.handler import ClassificationHandler
from apps.core.src.agent.orchestrator.features.classification.service import (
    OrchestratorClassificationService,
)
from apps.core.src.agent.orchestrator.features.context.handler import ContextLoaderHandler
from apps.core.src.agent.orchestrator.features.context.service import OrchestratorContextManager
from apps.core.src.agent.orchestrator.features.flow_context import FlowContextService
from apps.core.src.agent.orchestrator.features.fresh_start.handler import FreshStartHandler
from apps.core.src.agent.orchestrator.features.intent_routing.handler import IntentRoutingHandler
from apps.core.src.agent.orchestrator.features.intent_routing.service import (
    OrchestratorIntentRouter,
)
from apps.core.src.agent.orchestrator.features.message_quote import QuoteHandler, QuoteService
from apps.core.src.agent.orchestrator.features.task_planning.handler import NextTaskHandler
from apps.core.src.agent.orchestrator.features.task_planning.service import OrchestratorTaskPlanner
from apps.core.src.agent.orchestrator.flow_completion_callback import (
    OrchestratorFlowCompletionCallback,
)
from apps.core.src.agent.orchestrator.pipeline import MessageContext, MessagePipeline
from apps.core.src.agent.orchestrator.services import (
    ConversationResponder,
    TaskExecutor,
    TaskQueueService,
)
from apps.core.src.agent.sub_agents.account_management.service import AccountManagementService
from apps.core.src.agent.sub_agents.airtime import AirtimeService
from apps.core.src.agent.sub_agents.query.graph import QueryFlowGraph
from apps.core.src.agent.sub_agents.transfer import TransferService
from shared.clients.whatsapp.client import WhatsAppClient
from shared.repositories import BeneficiaryRepository, UserRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.utils.async_helpers import create_background_task


class OrchestratorAgent:
    """Orchestrator agent for the banking assistant."""

    def __init__(
        self,
        llm: ChatOpenAI,
        user_repo: UserRepository,
        beneficiary_repo: BeneficiaryRepository,
        actionable_message_repo: ActionableMessageRepository,
        whatsapp_client: WhatsAppClient,
        task_queue_service: TaskQueueService,
        conversation_responder: ConversationResponder,
        transfer_service: TransferService,
        airtime_service: AirtimeService,
        task_executor: TaskExecutor,
        query_graph: QueryFlowGraph,
        account_management_service: AccountManagementService,
        media_service: Any = None,
    ) -> None:
        self.llm = llm
        self.user_repo = user_repo
        self.whatsapp_client = whatsapp_client
        self.task_queue_service = task_queue_service
        self.conversation_responder = conversation_responder
        self.transfer_service = transfer_service
        self.airtime_service = airtime_service
        self.task_executor = task_executor
        self.query_graph = query_graph
        self.account_management_service = account_management_service
        self.media_service = media_service

        self.context_manager = OrchestratorContextManager(user_repo, beneficiary_repo)
        self.classification_service = OrchestratorClassificationService(llm)
        self.task_planner = OrchestratorTaskPlanner(llm, task_queue_service, task_executor)
        self.beneficiary_handler = OrchestratorBeneficiaryHandler(self.context_manager)
        self.completion_callback = OrchestratorFlowCompletionCallback(task_queue_service, self)
        self.cancellation_handler = OrchestratorCancellationHandler(
            transfer_service, airtime_service, self.context_manager, task_queue_service
        )
        self.flow_context_service = FlowContextService()
        self.intent_router = OrchestratorIntentRouter(
            task_queue_service,
            self.task_planner,
            transfer_service,
            airtime_service,
            conversation_responder,
            self.context_manager,
            query_graph,
            account_management_service,
            self.whatsapp_client,
            self.flow_context_service,
        )
        # Handler order matters
        self._handlers = [
            ContextLoaderHandler(self.context_manager, task_queue_service),
            ClassificationHandler(
                self.classification_service, self.context_manager, actionable_message_repo
            ),
            FreshStartHandler(self.context_manager, transfer_service, airtime_service),
            AffirmationHandler(transfer_service, airtime_service, self.flow_context_service, llm),
            QuoteHandler(
                QuoteService({"transfer": transfer_service, "airtime": airtime_service}),
                self.whatsapp_client,
            ),
            BeneficiaryHandler(self.beneficiary_handler),
            CancellationHandler(self.cancellation_handler),
            BatchAuthorizationHandler(task_queue_service, transfer_service, whatsapp_client),
            ActiveQueueHandler(task_queue_service, transfer_service, airtime_service),
            NextTaskHandler(self.task_planner),
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

        create_background_task(
            self.context_manager.add_conversation_turn(phone_number, "user", text)
        )
        create_background_task(
            self.context_manager.add_conversation_turn(phone_number, "assistant", response)
        )

        return response
