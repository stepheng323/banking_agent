"""Minimal orchestrator: LLM-based multilingual intent+complexity and user context cache."""

from typing import Any

from apps.core.src.agent.graphs.transfer import TransferService
from apps.core.src.agent.orchestrator.config import OrchestratorDependencies
from apps.core.src.agent.orchestrator.pipeline import MessageContext, MessagePipeline
from apps.core.src.agent.orchestrator.pipeline_stages.affirmation.handler import AffirmationHandler
from apps.core.src.agent.orchestrator.pipeline_stages.affirmation.service import FlowContextService
from apps.core.src.agent.orchestrator.pipeline_stages.batch_auth.handler import BatchAuthorizationHandler
from apps.core.src.agent.orchestrator.pipeline_stages.beneficiary.handler import BeneficiaryHandler
from apps.core.src.agent.orchestrator.pipeline_stages.beneficiary.service import OrchestratorBeneficiaryHandler
from apps.core.src.agent.orchestrator.pipeline_stages.classification.handler import ClassificationHandler
from apps.core.src.agent.orchestrator.pipeline_stages.classification.service import OrchestratorClassificationService
from apps.core.src.agent.orchestrator.pipeline_stages.context_loader.handler import ContextLoaderHandler
from apps.core.src.agent.orchestrator.pipeline_stages.context_loader.service import OrchestratorContextManager
from apps.core.src.agent.orchestrator.pipeline_stages.flow_control.handler import FlowControlHandler
from apps.core.src.agent.orchestrator.pipeline_stages.flow_control.service import OrchestratorCancellationHandler
from apps.core.src.agent.orchestrator.pipeline_stages.intent_routing.handler import IntentRoutingHandler
from apps.core.src.agent.orchestrator.pipeline_stages.intent_routing.router import OrchestratorIntentRouter
from apps.core.src.agent.orchestrator.pipeline_stages.quote.handler import QuoteHandler
from apps.core.src.agent.orchestrator.pipeline_stages.task_queue.coordination.coordinator import TaskCoordinator
from apps.core.src.agent.orchestrator.pipeline_stages.task_queue.handler import TaskQueueHandler
from apps.core.src.agent.orchestrator.pipeline_stages.task_queue.planner import OrchestratorTaskPlanner
from shared.utils.async_helpers import create_background_task
from apps.core.src.agent.orchestrator.pipeline_stages.intent_routing.deps import IntentRouterDependencies


class OrchestratorAgent:
    """Orchestrator agent for the banking assistant."""

    def __init__(self, deps: OrchestratorDependencies) -> None:
        self.deps = deps
        self.message_type = "text"

        self.context_manager = OrchestratorContextManager(deps.user_repo, deps.beneficiary_repo)
        self.classification_service = OrchestratorClassificationService(deps.llm)
        self.task_planner = OrchestratorTaskPlanner(deps.llm, deps.task_queue_service, deps.task_executor)
        self.beneficiary_handler = OrchestratorBeneficiaryHandler(self.context_manager)
        self.flow_context_service = FlowContextService()
        
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

        self.intent_router = self._build_intent_router()
        self._pipeline_stages = self._build_pipeline()

    def _build_intent_router(self) -> OrchestratorIntentRouter:
        """Build the intent router dependencies and service."""
        router_deps = IntentRouterDependencies(
            task_queue_service=self.deps.task_queue_service,
            task_planner=self.task_planner,
            transfer_service=self.deps.transfer_service,
            airtime_service=self.deps.airtime_service,
            conversation_responder=self.deps.conversation_responder,
            context_manager=self.context_manager,
            query_service=self.deps.query_service,
            whatsapp_client=self.deps.whatsapp_client,
            flow_context_service=self.flow_context_service,
            data_service=self.deps.data_service,
            account_management_service=self.deps.account_management_service,
            support_service=self.deps.support_service,
            faq_service=self.deps.faq_service,
        )
        return OrchestratorIntentRouter(router_deps)

    def _build_pipeline(self) -> list[Any]:
        """Build the message processing pipeline stages."""
        return [
            ContextLoaderHandler(self.context_manager, self.deps.task_queue_service),
            ClassificationHandler(self.classification_service, self.context_manager, self.deps.actionable_message_repo),
            FlowControlHandler(self.context_manager, self.cancellation_handler, self.deps.transfer_service, self.deps.airtime_service),
            AffirmationHandler(self.deps.transfer_service, self.deps.airtime_service, self.flow_context_service, self.deps.llm),
            QuoteHandler(self.deps.quote_service, self.deps.whatsapp_client),
            BeneficiaryHandler(self.beneficiary_handler),
            BatchAuthorizationHandler(self.deps.task_queue_service, self.deps.transfer_service, self.deps.whatsapp_client),
            TaskQueueHandler(self.deps.task_queue_service, self.task_planner, self.deps.transfer_service, self.deps.airtime_service, self.deps.executor_registry),
            IntentRoutingHandler(self.intent_router),
        ]

    @property
    def transfer(self) -> TransferService:
        """Get transfer service."""
        return self.deps.transfer_service

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
            text = await self.deps.media_service.process_audio(media_id)

        image_data = None
        if self.message_type == "image" and media_id:
            image_data = await self.deps.media_service.get_image_data(media_id)

        initial_context = MessageContext(
            phone_number=phone_number,
            text=text,
            message_id=message_id,
            image_data=image_data,
            quoted_message_id=quoted_message_id,
        )

        pipeline = MessagePipeline(self._pipeline_stages)
        response = await pipeline.process(initial_context)

        create_background_task(self.context_manager.add_conversation_turn(phone_number, "user", text))
        create_background_task(self.context_manager.add_conversation_turn(phone_number, "assistant", response))

        return response
