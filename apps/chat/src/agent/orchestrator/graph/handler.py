"""Orchestrator Graph Handler.

Integrates the Top-Level LangGraph into the Message Processing Pipeline.
"""

import asyncio
import random
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

import redis.asyncio as redis
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph.state import CompiledStateGraph

from apps.chat.src.agent.graphs.__shared__.beneficiary.suggestion_service import BeneficiarySuggestionService
from apps.chat.src.agent.orchestrator.context.referent_memory import referent_memory_ttl_seconds
from apps.chat.src.agent.orchestrator.graph import build_orchestrator_graph
from apps.chat.src.agent.orchestrator.models.message_context import MessageContext
from apps.chat.src.agent.orchestrator.nodes.cancellation import cancel_match_kind, is_obvious_cancel_message
from apps.chat.src.agent.orchestrator.nodes.gate.runner import (
    classify_deterministic_meta_response,
    classify_obvious_transfer_request,
)
from apps.chat.src.agent.orchestrator.presentation.intents import map_outbox_to_intents
from apps.chat.src.agent.orchestrator.progress import (
    MAX_PROGRESS_MESSAGES,
    PROGRESS_POLL_INTERVAL_SECONDS,
    TurnProgressTracker,
    is_progress_stage_user_visible,
    next_progress_delay_seconds,
    render_progress_message,
    seconds_until_progress_eligible,
    should_emit_progress,
)
from apps.chat.src.messaging.outbox import enqueue_outbox_say, enqueue_outbox_typing
from shared.cache.distributed_lock import RedisDistributedLock
from shared.clients.abstractions.banking import BankDataProvider
from shared.config.settings import settings
from shared.i18n import LocaleManager
from shared.protocols.worker import WorkerProtocol
from shared.queue.adapter import QueuePublisher
from shared.repositories.account_repository import AccountRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.user_repository import UserRepository
from shared.services.context_manager import ContextManager
from shared.services.conversation_grounding import attach_conversation_grounding, conversation_topic_for_response
from shared.services.conversation_responder import ConversationResponder
from shared.services.delivery_service import DeliveryAttemptResult
from shared.services.task_planner import OrchestratorTaskPlanner
from shared.utils.async_helpers import create_background_task
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class OrchestratorGraphHandler:
    """
    Handler that drives the LangGraph Orchestrator.
    """

    def __init__(
        self,
        task_planner: OrchestratorTaskPlanner,
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
        self._housekeeping_semaphore = asyncio.Semaphore(max(1, settings.async_housekeeping_max_concurrency))
        self._error_window: deque[int] = deque(maxlen=200)

        self.graph: CompiledStateGraph = build_orchestrator_graph(checkpointer=self.checkpointer)

    async def _deliver_progress_update(
        self,
        *,
        phone_number: str,
        channel: str,
        text: str,
        metadata: dict[str, Any],
        thread_id: str,
        stage_key: str,
        progress_count: int,
        turn_id: str,
    ) -> DeliveryAttemptResult:
        try:
            result = await enqueue_outbox_say(
                self.publisher,
                phone_number,
                channel,
                text,
                metadata=metadata,
            )
            logger.info(
                "progress_delivery_attempt",
                thread_id=thread_id,
                turn_id=turn_id,
                stage_key=stage_key,
                progress_count_before_attempt=progress_count,
                dedupe_key=metadata.get("dedupe_key"),
                delivery_status=result.status,
            )
            return result
        except Exception as exc:
            logger.warning(
                "progress_message_send_failed",
                thread_id=thread_id,
                turn_id=turn_id,
                stage_key=stage_key,
                progress_count=progress_count,
                error=str(exc),
            )
            return DeliveryAttemptResult(status="failed", error=str(exc))

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
        lock = RedisDistributedLock(
            self.redis_client,
            key=f"chat:thread-lock:{thread_id}",
            ttl_seconds=settings.chat_thread_lock_ttl_seconds,
        )
        await lock.acquire(wait_seconds=settings.chat_thread_lock_wait_seconds)
        renew_task = asyncio.create_task(
            lock.renew_periodically(interval_seconds=settings.chat_thread_lock_renew_seconds),
            name=f"chat-thread-lock-renew:{thread_id}",
        )
        try:
            logger.info(
                "orchestrator_thread_lock_acquired",
                thread_id=thread_id,
                ttl_seconds=settings.chat_thread_lock_ttl_seconds,
                renew_seconds=settings.chat_thread_lock_renew_seconds,
            )
            yield
        finally:
            renew_task.cancel()
            renew_results = await asyncio.gather(renew_task, return_exceptions=True)
            for result in renew_results:
                if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                    logger.warning("orchestrator_thread_lock_renew_failed", thread_id=thread_id, error=str(result))
            try:
                released = await lock.release()
            except Exception as exc:
                logger.warning("orchestrator_thread_lock_release_failed", thread_id=thread_id, error=str(exc))
            else:
                logger.info("orchestrator_thread_lock_released", thread_id=thread_id, released=released)

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

    @staticmethod
    def _resolve_path_label(context: MessageContext, final_state: dict[str, Any]) -> str:
        if getattr(context, "is_media_input", False) or bool(context.image_data):
            return "media_path"
        if final_state.get("direct_path_triggered"):
            return "direct_path"
        if final_state.get("last_interrupt") or final_state.get("pending_interrupt"):
            return "interrupt_path"
        return "planner_path"

    @staticmethod
    def _resolve_semantic_path_shape(
        context: MessageContext,
        final_state: dict[str, Any],
        path_label: str,
    ) -> str:
        explicit = final_state.get("semantic_path_shape")
        if isinstance(explicit, str) and explicit:
            return explicit
        if getattr(context, "is_media_input", False) or bool(context.image_data):
            return "media"
        if path_label == "planner_path":
            return "planner"
        return path_label

    def _log_latency_span(
        self,
        *,
        span: str,
        duration_ms: float,
        phone_number: str,
        path_label: str,
    ) -> None:
        logger.info(
            "perf_timer_latency",
            gate=span,
            span=span,
            path_label=path_label,
            duration_ms=round(duration_ms, 2),
            phone_number=phone_number,
        )

    def _log_semantic_path_shape(self, *, semantic_path_shape: str, path_label: str, phone_number: str) -> None:
        logger.info(
            "orchestrator_semantic_path",
            semantic_path_shape=semantic_path_shape,
            path_label=path_label,
            phone_number=phone_number,
        )

    @staticmethod
    def _task_executor_labels(final_state: dict[str, Any]) -> list[str]:
        labels: list[str] = []
        tasks = final_state.get("tasks") or {}
        if not isinstance(tasks, dict):
            return labels
        for spec in tasks.values():
            executor = getattr(spec, "type", None)
            if isinstance(executor, str) and executor and executor not in labels:
                labels.append(executor)
        return labels

    @staticmethod
    def _planner_primary_intent(final_state: dict[str, Any]) -> str | None:
        planner_output = final_state.get("planner_output")
        primary_intent = getattr(planner_output, "primary_intent", None)
        return primary_intent if isinstance(primary_intent, str) and primary_intent else None

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
        task_executors = self._task_executor_labels(final_state)
        task_map = final_state.get("tasks")
        task_count = len(task_map) if isinstance(task_map, dict) else len(task_executors)
        logger.info(
            "orchestrator_route_metrics",
            phone_number=phone_number,
            path_label=path_label,
            semantic_path_shape=semantic_path_shape,
            routing_owner=final_state.get("routing_owner"),
            routing_decision=final_state.get("routing_decision"),
            routing_target_domain=final_state.get("routing_target_domain"),
            routing_mode=final_state.get("routing_mode"),
            planner_used=bool(final_state.get("planner_used")),
            planner_primary_intent=self._planner_primary_intent(final_state),
            direct_path_triggered=bool(final_state.get("direct_path_triggered")),
            expected_transaction_executors=list(final_state.get("preplanner_expected_transaction_executors") or []),
            task_executors=task_executors,
            task_count=task_count,
            wave_count=len(final_state.get("waves") or []),
            progress_count=progress_count,
            total_duration_ms=round(total_duration_ms, 2),
        )

    def _record_guardrail_signal(self, *, path_label: str, duration_ms: float, errored: bool) -> None:
        self._error_window.append(1 if errored else 0)
        window_count = len(self._error_window)
        error_rate = (sum(self._error_window) / window_count) if window_count else 0.0
        breach_level = None
        if duration_ms > settings.latency_slo_p99_ms:
            breach_level = "p99"
        elif duration_ms > settings.latency_slo_p95_ms:
            breach_level = "p95"
        elif duration_ms > settings.latency_slo_p50_ms:
            breach_level = "p50"

        if breach_level:
            logger.warning(
                "latency_slo_breach",
                path_label=path_label,
                duration_ms=round(duration_ms, 2),
                threshold_ms=getattr(settings, f"latency_slo_{breach_level}_ms"),
                breach_level=breach_level,
            )
        if error_rate > settings.latency_slo_error_rate_threshold and window_count >= 20:
            logger.warning(
                "latency_rollback_signal",
                path_label=path_label,
                error_rate=round(error_rate, 4),
                threshold=settings.latency_slo_error_rate_threshold,
                recommendation="roll_back_recent_latency_changes",
            )

    async def _run_progress_updates(
        self,
        *,
        tracker: TurnProgressTracker,
        phone_number: str,
        channel: str,
        channel_identity: str | None = None,
        inbound_message_id: str | None,
        thread_id: str,
        turn_id: str,
        enable_initial_typing: bool,
    ) -> None:
        deduped_progress_keys: set[str] = set()
        delivery_target = channel_identity if channel != "whatsapp" and channel_identity else phone_number

        if enable_initial_typing:
            try:
                await enqueue_outbox_typing(
                    self.publisher,
                    delivery_target,
                    channel,
                    metadata={
                        "inbound_message_id": inbound_message_id,
                        "dedupe_key": f"{thread_id}:{turn_id}:typing:0",
                    },
                )
            except Exception as exc:
                logger.warning("initial_typing_indicator_failed", error=str(exc))

        try:
            while True:
                snapshot = await tracker.snapshot()
                if snapshot.progress_count >= MAX_PROGRESS_MESSAGES:
                    return

                if not snapshot.stage_key:
                    await tracker.wait_for_update(PROGRESS_POLL_INTERVAL_SECONDS)
                    continue

                if not is_progress_stage_user_visible(snapshot.stage_key):
                    await tracker.wait_for_update(PROGRESS_POLL_INTERVAL_SECONDS)
                    continue

                next_delay = next_progress_delay_seconds(snapshot.stage_key, snapshot.progress_count)
                if next_delay is None:
                    return

                wait_seconds = seconds_until_progress_eligible(snapshot)
                if wait_seconds is None:
                    return
                if not should_emit_progress(snapshot):
                    await tracker.wait_for_update(min(PROGRESS_POLL_INTERVAL_SECONDS, wait_seconds))
                    continue

                text = render_progress_message(
                    stage_key=snapshot.stage_key,
                    progress_count=snapshot.progress_count,
                    locale=snapshot.locale,
                    stage_metadata=snapshot.stage_metadata,
                )
                dedupe_key = f"{thread_id}:{turn_id}:progress:{snapshot.progress_count}:{snapshot.stage_key}"
                if dedupe_key in deduped_progress_keys:
                    await tracker.wait_for_update(PROGRESS_POLL_INTERVAL_SECONDS)
                    continue
                metadata = {
                    "dedupe_key": dedupe_key,
                    "progress_stage": snapshot.stage_key,
                    "progress_count": snapshot.progress_count,
                    "progress_turn_id": turn_id,
                    "inbound_message_id": inbound_message_id,
                    "force_typing_indicator": True,
                }
                delivery_task = asyncio.create_task(
                    self._deliver_progress_update(
                        phone_number=delivery_target,
                        channel=channel,
                        text=text,
                        metadata=metadata,
                        thread_id=thread_id,
                        stage_key=snapshot.stage_key,
                        progress_count=snapshot.progress_count,
                        turn_id=turn_id,
                    )
                )
                try:
                    delivery_result = await asyncio.shield(delivery_task)
                except asyncio.CancelledError:
                    await delivery_task
                    raise
                if delivery_result.delivered:
                    await tracker.record_progress_sent()
                    continue
                if delivery_result.status in {"deduped_completed", "deduped_resumed"}:
                    deduped_progress_keys.add(dedupe_key)
        except asyncio.CancelledError:
            raise

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
            pre_route_transfer_reason = None
            pre_route_cancel_kind = None
            pre_route_meta_response = None
            path_label = "planner_path"
            hydration_profile_mode: Literal["full", "minimal"] = "full"
            hydration_account_mode: Literal["full", "cache_only"] = "full"
            hydration_beneficiary_mode: Literal["full", "cache_only"] = "full"
            enable_initial_typing = True
            if getattr(context, "is_media_input", False) or bool(context.image_data):
                path_label = "media_path"
            else:
                pre_route_cancel_kind = (
                    cancel_match_kind(context.text) if is_obvious_cancel_message(context.text) else None
                )
                if pre_route_cancel_kind:
                    path_label = "cancel_path"
                    hydration_profile_mode = "minimal"
                    hydration_account_mode = "cache_only"
                    hydration_beneficiary_mode = "cache_only"
                    enable_initial_typing = False
                    logger.info(
                        "orchestrator_cancel_prefastpath",
                        phone_number=phone_number,
                        match_kind=pre_route_cancel_kind,
                    )
                else:
                    pre_route_transfer_reason = classify_obvious_transfer_request(context.text)
                if pre_route_transfer_reason:
                    path_label = "direct_path"
                    hydration_profile_mode = "minimal"
                    hydration_account_mode = "full"
                    hydration_beneficiary_mode = "cache_only"
                    enable_initial_typing = False
                elif not context.quoted_message_id:
                    pre_route_meta_response = classify_deterministic_meta_response(context.text)
                    if pre_route_meta_response:
                        response_key = pre_route_meta_response.response_key
                        response_locale = pre_route_meta_response.response_locale
                        path_label = "direct_path"
                        hydration_profile_mode = "minimal"
                        hydration_account_mode = "cache_only"
                        hydration_beneficiary_mode = "cache_only"
                        enable_initial_typing = False
                        logger.info(
                            "orchestrator_meta_prefastpath",
                            phone_number=phone_number,
                            response_key=response_key,
                            response_locale=response_locale,
                        )

            try:
                inputs = {
                    "user_id": phone_number,
                    "phone_number": phone_number,
                    "last_message_text": context.text,
                    "last_message_id": context.message_id,
                    "last_callback": None,
                    "has_quote": bool(context.quoted_message_id),
                    "quoted_message_id": context.quoted_message_id,
                    "channel": context.channel,
                    "channel_identity": context.channel_identity,
                }

                # Hydrate via ContextManager (Parallel Fetch)
                h_start = time.perf_counter()
                try:
                    user_ctx, _, _, _ = await self.context_manager.load_context_parallel(
                        phone_number,
                        path_label=path_label,
                        user=context.resolved_user,
                        profile_mode=hydration_profile_mode,
                        account_mode=hydration_account_mode,
                        beneficiary_mode=hydration_beneficiary_mode,
                    )
                except TypeError:
                    user_ctx, _, _, _ = await self.context_manager.load_context_parallel(phone_number)
                h_duration = (time.perf_counter() - h_start) * 1000

                loaded_context: dict[str, Any] = {
                    "profile": user_ctx.get("profile"),
                    "accounts": user_ctx.get("accounts"),
                    "beneficiaries": user_ctx.get("beneficiaries"),
                    "history": user_ctx.get("history", []),
                    "channel_metadata": dict(context.channel_metadata or {}),
                    "language": LocaleManager.normalize(user_ctx.get("language")).value,
                    "detected_language": LocaleManager.normalize(user_ctx.get("language")).value,
                    "user_id": user_ctx.get("profile", {}).get("id") if user_ctx.get("profile") else None,
                    "account_context_mode": hydration_account_mode,
                    "beneficiary_context_mode": hydration_beneficiary_mode,
                }
                loaded_context = attach_conversation_grounding(loaded_context)

                inputs["loaded_context"] = loaded_context

                config = self._get_config(phone_number, channel=context.channel)
                progress_tracker = TurnProgressTracker(locale=loaded_context["language"])
                config["configurable"]["progress_tracker"] = progress_tracker
                thread_id = config["configurable"]["thread_id"]
                turn_id = context.message_id or f"invoke-{time.monotonic_ns()}"
                progress_task = asyncio.create_task(
                    self._run_progress_updates(
                        tracker=progress_tracker,
                        phone_number=phone_number,
                        channel=context.channel,
                        channel_identity=context.channel_identity,
                        inbound_message_id=context.message_id,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        enable_initial_typing=enable_initial_typing,
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
                path_label = self._resolve_path_label(context, final_state)
                semantic_path_shape = self._resolve_semantic_path_shape(context, final_state, path_label)
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
                outbox = final_state.get("outbox", [])
                response_text = final_state.get("final_response")
                resolved_locale = LocaleManager.normalize(
                    (final_state.get("loaded_context") or {}).get("language") or loaded_context.get("language")
                ).value

                intents = map_outbox_to_intents(outbox, response_text)

                # Apply cleanup policy
                await self._run_housekeeping(
                    thread_id=thread_id,
                    state=final_state,
                    phone_number=phone_number,
                    path_label=path_label,
                )

                result = {
                    "text": response_text,
                    "intents": intents,
                    "outbox": outbox,  # Keep raw outbox for logging/debug if needed
                    "locale": resolved_locale,
                    "delivery_metadata": {},
                    "semantic_path_shape": semantic_path_shape,
                    "conversation_topic": final_state.get("conversation_topic")
                    or conversation_topic_for_response(
                        response_text,
                        semantic_path_shape=semantic_path_shape,
                        routing_decision=final_state.get("routing_decision"),
                    ),
                    "suppress_empty_fallback": bool(final_state.get("suppress_empty_fallback")),
                }
                if context.channel == "whatsapp":
                    typing_visibility_delay_ms = settings.whatsapp.typing_indicator_delay_ms
                elif context.channel == "telegram":
                    typing_visibility_delay_ms = settings.telegram_typing_indicator_delay_ms
                else:
                    typing_visibility_delay_ms = 0
                logger.info(
                    "orchestrator_progress_delivery_summary",
                    progress_stage=progress_snapshot.stage_key,
                    progress_count=progress_snapshot.progress_count,
                    visible_progress_sent=progress_snapshot.progress_count > 0,
                    typing_policy=(
                        "explicit_progress_typing_only" if enable_initial_typing else "suppressed_for_fastpath"
                    ),
                    typing_visibility_delay_ms=typing_visibility_delay_ms,
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

    async def _run_housekeeping(
        self,
        *,
        thread_id: str,
        state: dict[str, Any],
        phone_number: str,
        path_label: str,
    ) -> None:
        create_background_task(
            self._run_housekeeping_with_retries(
                thread_id=thread_id,
                state=state,
                phone_number=phone_number,
                path_label=path_label,
            ),
            task_name="orchestrator_housekeeping",
        )

    async def _run_housekeeping_with_retries(
        self,
        *,
        thread_id: str,
        state: dict[str, Any],
        phone_number: str,
        path_label: str,
    ) -> None:
        max_attempts = max(1, settings.async_housekeeping_max_retries + 1)
        for attempt in range(1, max_attempts + 1):
            async with self._housekeeping_semaphore:
                cleanup_start = time.perf_counter()
                cleanup_ok = await self._cleanup_if_idle(thread_id, state)
                cleanup_duration = (time.perf_counter() - cleanup_start) * 1000
                self._log_latency_span(
                    span="cleanup",
                    duration_ms=cleanup_duration,
                    phone_number=phone_number,
                    path_label=path_label,
                )

                ttl_start = time.perf_counter()
                ttl_ok = await self._maybe_apply_session_ttl(thread_id)
                ttl_duration = (time.perf_counter() - ttl_start) * 1000
                self._log_latency_span(
                    span="ttl_apply",
                    duration_ms=ttl_duration,
                    phone_number=phone_number,
                    path_label=path_label,
                )

            if cleanup_ok and ttl_ok:
                return
            if attempt >= max_attempts:
                logger.warning(
                    "housekeeping_retry_exhausted",
                    thread_id=thread_id,
                    attempts=attempt,
                    cleanup_ok=cleanup_ok,
                    ttl_ok=ttl_ok,
                )
                return
            backoff = (settings.async_housekeeping_retry_base_ms * attempt) / 1000.0
            await asyncio.sleep(backoff + random.uniform(0.01, 0.09))

    async def _maybe_apply_session_ttl(self, thread_id: str) -> bool:
        chat_ok = await self._apply_chat_history_ttl(thread_id)
        logger.info(
            "checkpoint_ttl_maintenance_skipped",
            thread_id=thread_id,
            reason="request_path_disabled",
            configured_interval_seconds=max(0, settings.checkpoint_ttl_maintenance_interval_seconds),
        )
        return chat_ok

    async def _expire_keys_with_ttl(self, keys: list[str], ttl: int) -> tuple[int, str]:
        if not keys:
            return 0, "no_keys"

        pipeline_factory = getattr(self.redis_client, "pipeline", None)
        if callable(pipeline_factory):
            pipe = pipeline_factory(transaction=False)
            for key in keys:
                pipe.expire(key, ttl)
            results = await pipe.execute()
            applied = sum(1 for result in results if result)
            return applied, "pipeline"

        applied = 0
        for key in keys:
            if await self.redis_client.expire(key, ttl):
                applied += 1
        return applied, "sequential"

    async def _apply_session_ttl(self, thread_id: str, ttl: int = 86400) -> bool:
        """Apply TTL to LangGraph checkpoint keys associated with a thread."""
        try:
            # Patterns for LangGraph Redis Saver keys
            patterns = [
                f"checkpoint:{thread_id}:*",
                f"checkpoint_write:{thread_id}:*",
                f"write_keys_zset:{thread_id}:*",
                f"checkpoint_latest:{thread_id}:*",
            ]

            total_start = time.perf_counter()
            total_matched = 0
            total_applied = 0
            for pattern in patterns:
                scan_start = time.perf_counter()
                keys: list[str] = []
                async for key in self.redis_client.scan_iter(match=pattern):
                    keys.append(key)
                scan_duration = (time.perf_counter() - scan_start) * 1000

                expire_start = time.perf_counter()
                applied_count, expire_mode = await self._expire_keys_with_ttl(keys, ttl)
                expire_duration = (time.perf_counter() - expire_start) * 1000

                total_matched += len(keys)
                total_applied += applied_count
                logger.info(
                    "checkpoint_ttl_pattern_processed",
                    thread_id=thread_id,
                    pattern=pattern,
                    matched_key_count=len(keys),
                    expire_applied_count=applied_count,
                    expire_mode=expire_mode,
                    scan_duration_ms=round(scan_duration, 2),
                    expire_duration_ms=round(expire_duration, 2),
                )

            logger.info(
                "checkpoint_ttl_apply_summary",
                thread_id=thread_id,
                pattern_count=len(patterns),
                matched_key_count=total_matched,
                expire_applied_count=total_applied,
                total_duration_ms=round((time.perf_counter() - total_start) * 1000, 2),
            )

            return True
        except Exception as e:
            logger.warning("apply_session_ttl_error", thread_id=thread_id, error=str(e))
            return False

    async def _apply_chat_history_ttl(self, thread_id: str, ttl: int = 86400) -> bool:
        try:
            start = time.perf_counter()
            key = f"user:{thread_id.split(':')[-1]}:chat_history"
            ok = await self.redis_client.expire(key, ttl)
            logger.info(
                "chat_history_ttl_refreshed",
                thread_id=thread_id,
                key=key,
                ttl=ttl,
                refreshed=bool(ok),
                duration_ms=round((time.perf_counter() - start) * 1000, 2),
            )
            return True
        except Exception as e:
            logger.warning("apply_chat_history_ttl_error", thread_id=thread_id, error=str(e))
            return False

    @staticmethod
    def _context_frame_ttl_seconds(state: dict[str, Any]) -> int:
        """Return remaining TTL for fresh structured result frames in an idle thread."""
        frames = state.get("context_frames") or []
        if not isinstance(frames, list):
            return 0

        now = int(time.time())
        remaining_seconds = 0
        for frame in frames:
            created_at = getattr(frame, "created_at_ts", None)
            ttl_seconds = getattr(frame, "ttl_seconds", None)
            if isinstance(frame, dict):
                created_at = frame.get("created_at_ts")
                ttl_seconds = frame.get("ttl_seconds")
            if not isinstance(created_at, int) or not isinstance(ttl_seconds, int):
                continue
            remaining_seconds = max(remaining_seconds, (created_at + ttl_seconds) - now)
        return max(0, remaining_seconds)

    @staticmethod
    def _capability_boundary_ttl_seconds(state: dict[str, Any]) -> int:
        """Return remaining TTL for unsupported-capability follow-up context."""
        boundary = state.get("capability_boundary")
        if not boundary:
            return 0

        last_updated = getattr(boundary, "last_updated_ts", None)
        ttl_seconds = getattr(boundary, "ttl_seconds", None)
        if isinstance(boundary, dict):
            last_updated = boundary.get("last_updated_ts")
            ttl_seconds = boundary.get("ttl_seconds")
        if not isinstance(last_updated, int | float) or not isinstance(ttl_seconds, int):
            return 0

        return max(0, int((float(last_updated) + ttl_seconds) - time.time()))

    async def _cleanup_if_idle(self, thread_id: str, state: dict[str, Any]) -> bool:
        """Explicitly delete thread if no active tasks, waves, or interruptions remain."""
        # Check if the state is truly "idle" (nothing pending)
        tasks = state.get("tasks", {})
        waves = state.get("waves", [])
        pending_interrupt = state.get("pending_interrupt")
        stashed_sessions = state.get("stashed_sessions", [])
        capability_boundary = state.get("capability_boundary")

        logger.info(
            "cleanup_check",
            thread_id=thread_id,
            has_tasks=bool(tasks),
            has_waves=bool(waves),
            has_interrupt=bool(pending_interrupt),
            has_stashed=bool(stashed_sessions),
            has_capability_boundary=bool(capability_boundary),
            task_count=len(tasks) if tasks else 0,
            wave_count=len(waves) if waves else 0,
        )

        if not tasks and not waves and not pending_interrupt and not stashed_sessions:
            context_frame_ttl = max(
                self._context_frame_ttl_seconds(state),
                referent_memory_ttl_seconds(state),
                self._capability_boundary_ttl_seconds(state),
            )
            if context_frame_ttl > 0:
                ttl_ok = await self._apply_session_ttl(thread_id, ttl=context_frame_ttl)
                logger.info(
                    "orchestrator_thread_retained_for_context_followup",
                    thread_id=thread_id,
                    context_frame_ttl_seconds=context_frame_ttl,
                    ttl_applied=ttl_ok,
                )
                return ttl_ok
            try:
                # adelete_thread is the proper way to wipe a thread in LangGraph
                await self.checkpointer.adelete_thread(thread_id)
                logger.info("orchestrator_thread_cleaned", thread_id=thread_id)
                return True
            except Exception as e:
                logger.warning("cleanup_thread_error", thread_id=thread_id, error=str(e))
                return False
        return True

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

                await self._run_housekeeping(
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
