"""Orchestrator Graph Handler.

Integrates the Top-Level LangGraph into the Message Processing Pipeline.
"""

import asyncio
import random
import time
from collections import deque
from typing import Any, Literal

import redis.asyncio as redis
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph.state import CompiledStateGraph

from apps.core.src.agent.graphs.__shared__.beneficiary.suggestion_service import BeneficiarySuggestionService
from apps.core.src.agent.orchestrator.graph import build_orchestrator_graph
from apps.core.src.agent.orchestrator.models.message_context import MessageContext
from apps.core.src.agent.orchestrator.presentation.intents import map_outbox_to_intents
from apps.core.src.agent.orchestrator.progress import (
    MAX_PROGRESS_MESSAGES,
    PROGRESS_POLL_INTERVAL_SECONDS,
    TurnProgressTracker,
    is_progress_stage_user_visible,
    next_progress_delay_seconds,
    render_progress_message,
    seconds_until_progress_eligible,
    should_emit_progress,
    should_suppress_followup_typing,
)
from apps.core.src.messaging.outbox import enqueue_outbox_say
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
        mode: Literal["planning", "execution", "both"] = "both",
    ):
        self.task_planner = task_planner
        self.redis_client = redis_client
        self.publisher = publisher
        self.beneficiary_suggestion_service = beneficiary_suggestion_service
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
        self._invoke_lock = asyncio.Lock()
        self._housekeeping_semaphore = asyncio.Semaphore(max(1, settings.async_housekeeping_max_concurrency))
        self._ttl_maintenance_lock = asyncio.Lock()
        self._next_ttl_maintenance_at = 0.0
        self._error_window: deque[int] = deque(maxlen=200)

        self.graph: CompiledStateGraph = build_orchestrator_graph(checkpointer=self.checkpointer)

    @staticmethod
    def _delivery_metadata_from_progress_snapshot(snapshot: Any) -> dict[str, Any]:
        if should_suppress_followup_typing(snapshot):
            return {"suppress_typing_indicator": True}
        return {}

    async def _deliver_progress_update(
        self,
        *,
        tracker: TurnProgressTracker,
        phone_number: str,
        channel: str,
        text: str,
        metadata: dict[str, Any],
        thread_id: str,
        stage_key: str,
        progress_count: int,
    ) -> None:
        try:
            await enqueue_outbox_say(
                self.publisher,
                phone_number,
                channel,
                text,
                metadata=metadata,
            )
        except Exception as exc:
            logger.warning(
                "progress_message_send_failed",
                thread_id=thread_id,
                stage_key=stage_key,
                progress_count=progress_count,
                error=str(exc),
            )
        finally:
            await tracker.record_progress_sent()

    async def _ensure_checkpointer(self) -> None:
        """Ensure checkpointer is initialized."""
        if not self._checkpointer_setup:
            await self.checkpointer.asetup()
            self._checkpointer_setup = True

    def _get_config(self, phone_number: str, channel: str) -> RunnableConfig:
        """Create LangGraph configuration."""
        thread_id = f"{channel}:{phone_number}"
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
            },
            "recursion_limit": 50,
        }

    @staticmethod
    def _resolve_path_label(context: MessageContext, final_state: dict[str, Any]) -> str:
        if getattr(context, "is_media_input", False) or bool(context.image_data):
            return "media_path"
        if final_state.get("fast_path_triggered"):
            return "fast_path"
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
        channel_identity: str | None,
        inbound_message_id: str | None,
        thread_id: str,
    ) -> None:
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
                metadata = {
                    "dedupe_key": f"{thread_id}:progress:{snapshot.progress_count}:{snapshot.stage_key}",
                    "progress_stage": snapshot.stage_key,
                    "progress_count": snapshot.progress_count,
                    "force_typing_indicator": True,
                }
                delivery_task = asyncio.create_task(
                    self._deliver_progress_update(
                        tracker=tracker,
                        phone_number=phone_number,
                        channel=channel,
                        text=text,
                        metadata=metadata,
                        thread_id=thread_id,
                        stage_key=snapshot.stage_key,
                        progress_count=snapshot.progress_count,
                    )
                )
                try:
                    await asyncio.shield(delivery_task)
                except asyncio.CancelledError:
                    await delivery_task
                    raise
        except asyncio.CancelledError:
            raise

    async def invoke(self, context: MessageContext) -> dict[str, Any]:
        """
        Run the graph.

        Returns:
            str: Response message if any
            None: If no response generated
        """
        async with self._invoke_lock:
            await self._ensure_checkpointer()
            turn_start = time.perf_counter()

            phone_number = context.phone_number
            path_label = (
                "media_path"
                if (getattr(context, "is_media_input", False) or bool(context.image_data))
                else "planner_path"
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
                    )
                except TypeError:
                    user_ctx, _, _, _ = await self.context_manager.load_context_parallel(phone_number)
                h_duration = (time.perf_counter() - h_start) * 1000

                loaded_context: dict[str, Any] = {
                    "profile": user_ctx.get("profile"),
                    "accounts": user_ctx.get("accounts"),
                    "beneficiaries": user_ctx.get("beneficiaries"),
                    "history": user_ctx.get("history", []),
                    "language": LocaleManager.normalize(user_ctx.get("language")).value,
                    "detected_language": LocaleManager.normalize(user_ctx.get("language")).value,
                    "user_id": user_ctx.get("profile", {}).get("id") if user_ctx.get("profile") else None,
                }

                inputs["loaded_context"] = loaded_context

                config = self._get_config(phone_number, channel=context.channel)
                progress_tracker = TurnProgressTracker(locale=loaded_context["language"])
                config["configurable"]["progress_tracker"] = progress_tracker
                thread_id = config["configurable"]["thread_id"]
                progress_task = asyncio.create_task(
                    self._run_progress_updates(
                        tracker=progress_tracker,
                        phone_number=phone_number,
                        channel=context.channel,
                        channel_identity=context.channel_identity,
                        inbound_message_id=context.message_id,
                        thread_id=thread_id,
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
                    "delivery_metadata": self._delivery_metadata_from_progress_snapshot(progress_snapshot),
                }
                logger.info(
                    "orchestrator_progress_delivery_summary",
                    progress_stage=progress_snapshot.stage_key,
                    progress_count=progress_snapshot.progress_count,
                    visible_progress_sent=progress_snapshot.progress_count > 0,
                    suppress_followup_typing=bool(result["delivery_metadata"].get("suppress_typing_indicator")),
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
        interval = max(0, settings.checkpoint_ttl_maintenance_interval_seconds)
        if interval <= 0:
            checkpoint_ok = await self._apply_session_ttl(thread_id)
            return chat_ok and checkpoint_ok

        now = time.monotonic()
        if now < self._next_ttl_maintenance_at:
            return chat_ok

        async with self._ttl_maintenance_lock:
            now = time.monotonic()
            if now < self._next_ttl_maintenance_at:
                return chat_ok
            ok = await self._apply_session_ttl(thread_id)
            if ok:
                self._next_ttl_maintenance_at = now + interval
            return chat_ok and ok

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

            for pattern in patterns:
                async for key in self.redis_client.scan_iter(match=pattern):
                    await self.redis_client.expire(key, ttl)

            return True
        except Exception as e:
            logger.warning("apply_session_ttl_error", thread_id=thread_id, error=str(e))
            return False

    async def _apply_chat_history_ttl(self, thread_id: str, ttl: int = 86400) -> bool:
        try:
            await self.redis_client.expire(f"user:{thread_id.split(':')[-1]}:chat_history", ttl)
            return True
        except Exception as e:
            logger.warning("apply_chat_history_ttl_error", thread_id=thread_id, error=str(e))
            return False

    async def _cleanup_if_idle(self, thread_id: str, state: dict[str, Any]) -> bool:
        """Explicitly delete thread if no active tasks, waves, or interruptions remain."""
        # Check if the state is truly "idle" (nothing pending)
        tasks = state.get("tasks", {})
        waves = state.get("waves", [])
        pending_interrupt = state.get("pending_interrupt")
        stashed_sessions = state.get("stashed_sessions", [])

        logger.info(
            "cleanup_check",
            thread_id=thread_id,
            has_tasks=bool(tasks),
            has_waves=bool(waves),
            has_interrupt=bool(pending_interrupt),
            has_stashed=bool(stashed_sessions),
            task_count=len(tasks) if tasks else 0,
            wave_count=len(waves) if waves else 0,
        )

        if not tasks and not waves and not pending_interrupt and not stashed_sessions:
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

        async with self._invoke_lock:
            await self._ensure_checkpointer()
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

                thread_id = config["configurable"]["thread_id"]
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
