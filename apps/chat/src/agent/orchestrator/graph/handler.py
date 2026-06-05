"""Orchestrator graph handler facade."""

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

import redis.asyncio as redis
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph.state import CompiledStateGraph

from apps.chat.src.agent.orchestrator.context.context_manager import ContextManager
from apps.chat.src.agent.orchestrator.conversation.conversation_responder import ConversationResponder
from apps.chat.src.agent.orchestrator.graph import build_orchestrator_graph
from apps.chat.src.agent.orchestrator.graph.housekeeping import OrchestratorHousekeeping
from apps.chat.src.agent.orchestrator.graph.invocation_runner import GraphInvocationRunner
from apps.chat.src.agent.orchestrator.graph.progress import TurnProgressTracker
from apps.chat.src.agent.orchestrator.graph.progress_delivery import OrchestratorProgressDelivery
from apps.chat.src.agent.orchestrator.graph.resume_runner import GraphResumeRunner
from apps.chat.src.agent.orchestrator.graph.route_metrics import log_latency_span
from apps.chat.src.agent.orchestrator.graph.runtime import (
    GraphConfigDependencies,
    GraphRunnableConfig,
    build_graph_runnable_config,
    graph_thread_id,
)
from apps.chat.src.agent.orchestrator.graph.thread_lock import thread_invocation_lock
from apps.chat.src.agent.orchestrator.models.message_context import MessageContext
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner import TaskPlanner
from banking.accounts.repositories.account_repository import AccountRepository
from banking.beneficiaries.repositories.beneficiary_repository import BeneficiaryRepository
from banking.beneficiaries.services.suggestion_service import BeneficiarySuggestionService
from banking.identity.repositories.user_repository import UserRepository
from banking.messaging.repositories.actionable_message_repository import ActionableMessageRepository
from banking.runtime.protocols import WorkerProtocol
from shared.clients.abstractions.banking import BankDataProvider
from shared.queue.adapter import QueuePublisher
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _log_housekeeping_latency_span(
    *,
    span: str,
    duration_ms: float,
    phone_number: str,
    path_label: str,
) -> None:
    log_latency_span(
        logger,
        span=span,
        duration_ms=duration_ms,
        phone_number=phone_number,
        path_label=path_label,
    )


class OrchestratorGraphHandler:
    """Handler that drives the LangGraph orchestrator."""

    def __init__(
        self,
        task_planner: TaskPlanner,
        transfer_service: WorkerProtocol,
        airtime_service: WorkerProtocol,
        query_service: WorkerProtocol,
        data_service: WorkerProtocol,
        account_service: WorkerProtocol,
        beneficiary_service: WorkerProtocol,
        support_service: WorkerProtocol,
        faq_service: WorkerProtocol,
        user_repo: UserRepository,
        beneficiary_repo: BeneficiaryRepository,
        account_repo: AccountRepository,
        actionable_message_repo: ActionableMessageRepository,
        banking_provider: BankDataProvider,
        context_manager: ContextManager,
        redis_client: redis.Redis,
        publisher: QueuePublisher,
        beneficiary_suggestion_service: BeneficiarySuggestionService,
        conversation_responder: ConversationResponder | None = None,
        mode: Literal["planning", "execution", "both"] = "both",
    ):
        self.task_planner = task_planner
        self.redis_client = redis_client
        self.publisher = publisher
        self.progress_delivery = OrchestratorProgressDelivery(publisher)
        self.beneficiary_suggestion_service = beneficiary_suggestion_service
        self.conversation_responder = conversation_responder
        self.mode = mode
        self.context_manager = context_manager

        self.user_repo = user_repo
        self.beneficiary_repo = beneficiary_repo
        self.account_repo = account_repo
        self.actionable_message_repo = actionable_message_repo
        self.banking_provider = banking_provider

        self.services = {
            "transfer": transfer_service,
            "airtime": airtime_service,
            "query": query_service,
            "data": data_service,
            "account": account_service,
            "beneficiary": beneficiary_service,
            "support": support_service,
            "faq": faq_service,
        }

        self.checkpointer = AsyncRedisSaver(redis_client=redis_client)
        self._checkpointer_setup = False
        self._checkpointer_setup_lock = asyncio.Lock()
        self._error_window: deque[int] = deque(maxlen=200)

        self.graph: CompiledStateGraph = build_orchestrator_graph(checkpointer=self.checkpointer)
        self.housekeeping = OrchestratorHousekeeping(
            redis_client=redis_client,
            checkpointer=self.checkpointer,
            log_latency_span=_log_housekeeping_latency_span,
        )
        self.invocation_runner = GraphInvocationRunner(
            graph=self.graph,
            context_manager=self.context_manager,
            progress_delivery=self.progress_delivery,
            housekeeping=self.housekeeping,
            get_config=self._get_config,
            error_window=self._error_window,
            logger=logger,
            progress_tracker_factory=TurnProgressTracker,
        )
        self.resume_runner = GraphResumeRunner(
            graph=self.graph,
            housekeeping=self.housekeeping,
            get_config=self._get_config,
            logger=logger,
        )

    async def _ensure_checkpointer(self) -> None:
        """Ensure checkpointer is initialized."""
        if self._checkpointer_setup:
            return
        async with self._checkpointer_setup_lock:
            if self._checkpointer_setup:
                return
            await self.checkpointer.asetup()
            self._checkpointer_setup = True

    @staticmethod
    def _thread_id(phone_number: str, channel: str) -> str:
        return graph_thread_id(phone_number, channel)

    @asynccontextmanager
    async def _thread_invocation_lock(self, thread_id: str) -> AsyncIterator[None]:
        async with thread_invocation_lock(self.redis_client, thread_id, logger=logger):
            yield

    def _graph_config_dependencies(self) -> GraphConfigDependencies:
        return GraphConfigDependencies(
            task_planner=self.task_planner,
            services=self.services,
            user_repo=self.user_repo,
            beneficiary_repo=self.beneficiary_repo,
            account_repo=self.account_repo,
            actionable_message_repo=self.actionable_message_repo,
            banking_provider=self.banking_provider,
            beneficiary_suggestion_service=self.beneficiary_suggestion_service,
            redis_client=self.redis_client,
            publisher=self.publisher,
            conversation_responder=self.conversation_responder,
        )

    def _get_config(
        self,
        phone_number: str,
        channel: str,
        *,
        progress_tracker: TurnProgressTracker | None = None,
    ) -> GraphRunnableConfig:
        """Create LangGraph configuration."""
        return build_graph_runnable_config(
            phone_number=phone_number,
            channel=channel,
            dependencies=self._graph_config_dependencies(),
            progress_tracker=progress_tracker,
        )

    async def invoke(self, context: MessageContext) -> dict[str, Any]:
        """Run the graph for one inbound message."""
        await self._ensure_checkpointer()
        thread_id = self._thread_id(context.phone_number, context.channel)
        async with self._thread_invocation_lock(thread_id):
            return await self.invocation_runner.run(context)

    async def resume_flow(self, phone_number: str, payload: dict[str, Any], channel: str) -> dict[str, Any]:
        """Resume flow externally, for example from an auth callback."""
        await self._ensure_checkpointer()
        thread_id = self._thread_id(phone_number, channel)
        async with self._thread_invocation_lock(thread_id):
            return await self.resume_runner.run(phone_number=phone_number, payload=payload, channel=channel)
