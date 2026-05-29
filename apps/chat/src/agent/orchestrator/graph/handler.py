"""Orchestrator Graph Handler.

Integrates the Top-Level LangGraph into the Message Processing Pipeline.
"""

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

import redis.asyncio as redis
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph.state import CompiledStateGraph

from apps.chat.src.agent.orchestrator.graph import build_orchestrator_graph
from apps.chat.src.agent.orchestrator.graph.housekeeping import OrchestratorHousekeeping
from apps.chat.src.agent.orchestrator.graph.invocation_context import (
    build_graph_inputs,
    build_invocation_result,
    load_invocation_context,
    typing_visibility_delay_ms,
)
from apps.chat.src.agent.orchestrator.graph.preflight import plan_invocation_preflight
from apps.chat.src.agent.orchestrator.graph.progress import TurnProgressTracker
from apps.chat.src.agent.orchestrator.graph.progress_delivery import OrchestratorProgressDelivery
from apps.chat.src.agent.orchestrator.graph.route_metrics import (
    log_latency_span,
    log_route_metrics,
    log_semantic_path_shape,
    record_guardrail_signal,
    resolve_path_label,
    resolve_semantic_path_shape,
)
from apps.chat.src.agent.orchestrator.graph.thread_lock import thread_invocation_lock
from apps.chat.src.agent.orchestrator.models.message_context import MessageContext
from apps.chat.src.agent.workers.__shared__.beneficiary.suggestion_service import BeneficiarySuggestionService
from shared.clients.abstractions.banking import BankDataProvider
from shared.i18n.locale import LocaleManager
from shared.protocols.worker import WorkerProtocol
from shared.queue.adapter import QueuePublisher
from banking.accounts.repositories.account_repository import AccountRepository
from banking.messaging.repositories.actionable_message_repository import ActionableMessageRepository
from banking.beneficiaries.repositories.beneficiary_repository import BeneficiaryRepository
from banking.identity.repositories.user_repository import UserRepository
from apps.chat.src.agent.orchestrator.context.context_manager import ContextManager
from apps.chat.src.agent.orchestrator.conversation.conversation_responder import ConversationResponder
from apps.chat.src.agent.orchestrator.planning.task_planner import TaskPlanner
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class OrchestratorGraphHandler:
    """
    Handler that drives the LangGraph Orchestrator.
    """

    def __init__(
        self,
        task_planner: TaskPlanner,
        transfer_service: WorkerProtocol,
        airtime_service: WorkerProtocol,
        query_service: WorkerProtocol,
        data_service: WorkerProtocol,
        account_service: WorkerProtocol,
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
            log_latency_span=self._log_latency_span,
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
        return f"{channel}:{phone_number}"

    @asynccontextmanager
    async def _thread_invocation_lock(self, thread_id: str) -> AsyncIterator[None]:
        async with thread_invocation_lock(self.redis_client, thread_id, logger=logger):
            yield

    def _get_config(self, phone_number: str, channel: str) -> RunnableConfig:
        """Create LangGraph configuration."""
        thread_id = self._thread_id(phone_number, channel)
        return {
            "configurable": {
                "thread_id": thread_id,
                "task_planner": self.task_planner,
                "services": self.services,
                "user_repo": self.user_repo,
                "beneficiary_repo": self.beneficiary_repo,
                "account_repo": self.account_repo,
                "actionable_message_repo": self.actionable_message_repo,
                "banking_provider": self.banking_provider,
                "beneficiary_suggestion_service": self.beneficiary_suggestion_service,
                "redis_client": self.redis_client,
                "publisher": self.publisher,
                "conversation_responder": self.conversation_responder,
            },
            "recursion_limit": 50,
        }

    def _log_latency_span(
        self,
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

    def _log_semantic_path_shape(self, *, semantic_path_shape: str, path_label: str, phone_number: str) -> None:
        log_semantic_path_shape(
            logger,
            semantic_path_shape=semantic_path_shape,
            path_label=path_label,
            phone_number=phone_number,
        )

    def _log_route_metrics(
        self,
        *,
        final_state: dict[str, Any],
        phone_number: str,
        path_label: str,
        semantic_path_shape: str,
        total_duration_ms: float,
        progress_count: int,
    ) -> None:
        log_route_metrics(
            logger,
            final_state=final_state,
            phone_number=phone_number,
            path_label=path_label,
            semantic_path_shape=semantic_path_shape,
            total_duration_ms=total_duration_ms,
            progress_count=progress_count,
        )

    def _record_guardrail_signal(self, *, path_label: str, duration_ms: float, errored: bool) -> None:
        record_guardrail_signal(
            self._error_window,
            logger,
            path_label=path_label,
            duration_ms=duration_ms,
            errored=errored,
        )

    async def invoke(self, context: MessageContext) -> dict[str, Any]:
        """
        Run the graph.

        Returns:
            str: Response message if any
            None: If no response generated
        """
        await self._ensure_checkpointer()
        thread_id = self._thread_id(context.phone_number, context.channel)
        async with self._thread_invocation_lock(thread_id):
            turn_start = time.perf_counter()

            phone_number = context.phone_number
            preflight = plan_invocation_preflight(context, logger=logger)
            path_label = preflight.path_label

            try:
                inputs = build_graph_inputs(context)

                # Hydrate via ContextManager (Parallel Fetch)
                h_start = time.perf_counter()
                loaded_context = await load_invocation_context(
                    context_manager=self.context_manager,
                    context=context,
                    preflight=preflight,
                    path_label=path_label,
                )
                h_duration = (time.perf_counter() - h_start) * 1000

                inputs["loaded_context"] = loaded_context

                config = self._get_config(phone_number, channel=context.channel)
                progress_tracker = TurnProgressTracker(locale=loaded_context["language"])
                config["configurable"]["progress_tracker"] = progress_tracker
                thread_id = config["configurable"]["thread_id"]
                turn_id = context.message_id or f"invoke-{time.monotonic_ns()}"
                progress_task = asyncio.create_task(
                    self.progress_delivery.run_updates(
                        tracker=progress_tracker,
                        phone_number=phone_number,
                        channel=context.channel,
                        channel_identity=context.channel_identity,
                        inbound_message_id=context.message_id,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        enable_initial_typing=preflight.enable_initial_typing,
                    ),
                    name="orchestrator_progress_updates",
                )

                logger.info("orchestrator_graph_invoke", user=phone_number)

                g_start = time.perf_counter()
                try:
                    final_state = await self.graph.ainvoke(inputs, config=config)
                finally:
                    progress_task.cancel()
                    try:
                        await progress_task
                    except asyncio.CancelledError:
                        pass
                progress_snapshot = await progress_tracker.snapshot()
                g_duration = (time.perf_counter() - g_start) * 1000
                path_label = resolve_path_label(context, final_state)
                semantic_path_shape = resolve_semantic_path_shape(context, final_state, path_label)
                self._log_latency_span(
                    span="context_hydration",
                    duration_ms=h_duration,
                    phone_number=phone_number,
                    path_label=path_label,
                )
                self._log_latency_span(
                    span="graph_execution",
                    duration_ms=g_duration,
                    phone_number=phone_number,
                    path_label=path_label,
                )
                # Apply cleanup policy
                await self.housekeeping.run(
                    thread_id=thread_id,
                    state=final_state,
                    phone_number=phone_number,
                    path_label=path_label,
                )

                result = build_invocation_result(
                    final_state=final_state,
                    loaded_context=loaded_context,
                    semantic_path_shape=semantic_path_shape,
                )
                logger.info(
                    "orchestrator_progress_delivery_summary",
                    progress_stage=progress_snapshot.stage_key,
                    progress_count=progress_snapshot.progress_count,
                    visible_progress_sent=progress_snapshot.progress_count > 0,
                    typing_policy=(
                        "explicit_progress_typing_only"
                        if preflight.enable_initial_typing
                        else "suppressed_for_fastpath"
                    ),
                    typing_visibility_delay_ms=typing_visibility_delay_ms(context.channel),
                )
                total_duration = (time.perf_counter() - turn_start) * 1000
                self._log_latency_span(
                    span="orchestrator_turn_total",
                    duration_ms=total_duration,
                    phone_number=phone_number,
                    path_label=path_label,
                )
                logger.info("orchestrator_path_label", path_label=path_label, phone_number=phone_number)
                self._log_semantic_path_shape(
                    semantic_path_shape=semantic_path_shape,
                    path_label=path_label,
                    phone_number=phone_number,
                )
                self._log_route_metrics(
                    final_state=final_state,
                    phone_number=phone_number,
                    path_label=path_label,
                    semantic_path_shape=semantic_path_shape,
                    total_duration_ms=total_duration,
                    progress_count=progress_snapshot.progress_count,
                )
                self._record_guardrail_signal(path_label=path_label, duration_ms=total_duration, errored=False)
                return result
            except Exception:
                total_duration = (time.perf_counter() - turn_start) * 1000
                self._log_latency_span(
                    span="orchestrator_turn_total",
                    duration_ms=total_duration,
                    phone_number=phone_number,
                    path_label=path_label,
                )
                self._record_guardrail_signal(path_label=path_label, duration_ms=total_duration, errored=True)
                raise

    async def resume_flow(self, phone_number: str, payload: dict[str, Any], channel: str) -> dict[str, Any]:
        """Resume flow externally (e.g. from auth callback)."""

        await self._ensure_checkpointer()
        thread_id = self._thread_id(phone_number, channel)
        async with self._thread_invocation_lock(thread_id):
            inputs = {
                "user_id": phone_number,
                "phone_number": phone_number,
                "last_callback": payload,
                "has_quote": False,
                "quoted_message_id": None,
            }

            config = self._get_config(phone_number, channel=channel)

            logger.info("orchestrator_graph_resume", user=phone_number, payload=payload)

            try:
                final_state = await self.graph.ainvoke(inputs, config=config)
                resolved_locale = LocaleManager.normalize(
                    (final_state.get("loaded_context") or {}).get("language")
                ).value

                await self.housekeeping.run(
                    thread_id=thread_id,
                    state=final_state,
                    phone_number=phone_number,
                    path_label="interrupt_path",
                )

                return {
                    "text": final_state.get("final_response"),
                    "outbox": final_state.get("outbox", []),
                    "locale": resolved_locale,
                }
            except Exception as e:
                logger.exception("graph_resume_error", error=str(e))
                return {"text": None, "outbox": [], "locale": LocaleManager.DEFAULT_LOCALE.value}
