"""Orchestrator Graph Handler.

Integrates the Top-Level LangGraph into the Message Processing Pipeline.
"""

import time
from typing import Any, Literal

import redis.asyncio as redis
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph.state import CompiledStateGraph

from apps.core.src.agent.graphs.__shared__.beneficiary.suggestion_service import BeneficiarySuggestionService
from apps.core.src.agent.orchestrator.graph import build_orchestrator_graph
from apps.core.src.agent.orchestrator.models.message_context import MessageContext
from apps.core.src.agent.orchestrator.presentation.intents import map_outbox_to_intents
from shared.clients.abstractions.banking import BankingDataProvider
from shared.i18n import LocaleManager
from shared.protocols.worker import WorkerProtocol
from shared.queue.adapter import QueuePublisher
from shared.repositories.account_repository import AccountRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.user_repository import UserRepository
from shared.services.context_manager import ContextManager
from shared.services.task_planner import OrchestratorTaskPlanner
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
        banking_provider: BankingDataProvider,
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

        self.graph: CompiledStateGraph = build_orchestrator_graph(checkpointer=self.checkpointer)

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

    async def invoke(self, context: MessageContext) -> dict[str, Any]:
        """
        Run the graph.

        Returns:
            str: Response message if any
            None: If no response generated
        """
        await self._ensure_checkpointer()

        phone_number = context.phone_number

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
        user_ctx, _, _, _ = await self.context_manager.load_context_parallel(phone_number)
        h_duration = (time.perf_counter() - h_start) * 1000
        logger.info(
            "perf_timer_latency",
            gate="orchestrator_context_hydration",
            duration_ms=round(h_duration, 2),
            phone_number=phone_number,
        )

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

        logger.info("orchestrator_graph_invoke", user=phone_number)

        g_start = time.perf_counter()
        final_state = await self.graph.ainvoke(inputs, config=config)
        g_duration = (time.perf_counter() - g_start) * 1000
        logger.info(
            "perf_timer_latency",
            gate="orchestrator_graph_execution",
            duration_ms=round(g_duration, 2),
            phone_number=phone_number,
        )
        outbox = final_state.get("outbox", [])
        response_text = final_state.get("final_response")
        resolved_locale = LocaleManager.normalize(
            (final_state.get("loaded_context") or {}).get("language") or loaded_context.get("language")
        ).value

        intents = map_outbox_to_intents(outbox, response_text)

        # Apply cleanup policy
        thread_id = config["configurable"]["thread_id"]
        await self._cleanup_if_idle(thread_id, final_state)
        await self._apply_session_ttl(thread_id)

        return {
            "text": response_text,
            "intents": intents,
            "outbox": outbox,  # Keep raw outbox for logging/debug if needed
            "locale": resolved_locale,
        }

    async def _apply_session_ttl(self, thread_id: str, ttl: int = 86400) -> None:
        """Apply TTL to all Redis keys associated with a thread to prevent bloat."""
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

            # Also expire the chat history key overseen by ContextManager
            await self.redis_client.expire(f"user:{thread_id.split(':')[-1]}:chat_history", ttl)
        except Exception as e:
            logger.warning("apply_session_ttl_error", thread_id=thread_id, error=str(e))

    async def _cleanup_if_idle(self, thread_id: str, state: dict[str, Any]) -> None:
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
            except Exception as e:
                logger.warning("cleanup_thread_error", thread_id=thread_id, error=str(e))

    async def resume_flow(self, phone_number: str, payload: dict[str, Any], channel: str) -> dict[str, Any]:
        """Resume flow externally (e.g. from auth callback)."""

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
            resolved_locale = LocaleManager.normalize((final_state.get("loaded_context") or {}).get("language")).value

            # Apply cleanup policy
            thread_id = config["configurable"]["thread_id"]
            await self._cleanup_if_idle(thread_id, final_state)
            await self._apply_session_ttl(thread_id)

            return {
                "text": final_state.get("final_response"),
                "outbox": final_state.get("outbox", []),
                "locale": resolved_locale,
            }
        except Exception as e:
            logger.exception("graph_resume_error", error=str(e))
            return {"text": None, "outbox": [], "locale": LocaleManager.DEFAULT_LOCALE.value}
