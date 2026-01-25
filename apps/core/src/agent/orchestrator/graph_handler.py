"""Orchestrator Graph Handler.

Integrates the Top-Level LangGraph into the Message Processing Pipeline.
"""

from typing import Any, Literal

import redis.asyncio as redis
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph.state import CompiledStateGraph

from apps.core.src.agent.graphs.__shared__.beneficiary.suggestion_service import BeneficiarySuggestionService
from apps.core.src.agent.orchestrator.graph import build_orchestrator_graph
from apps.core.src.agent.orchestrator.intents import (
    RequestAuth,
    RequestConfirmation,
    Say,
    ShowFlow,
    ShowReceipt,
    UiIntent,
)
from apps.core.src.agent.orchestrator.models.message_context import MessageContext
from shared.clients.abstractions.banking import BankingDataProvider
from shared.clients.whatsapp.client import WhatsAppClient
from shared.protocols.worker import WorkerProtocol
from shared.queue.redis_queue import RedisQueue
from shared.repositories.account_repository import AccountRepository
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
        banking_provider: BankingDataProvider,
        context_manager: ContextManager,
        redis_client: redis.Redis,
        whatsapp_client: WhatsAppClient,
        queue: RedisQueue,
        beneficiary_suggestion_service: BeneficiarySuggestionService,
        mode: Literal["planning", "execution", "both"] = "both",
    ):
        self.task_planner = task_planner
        self.redis_client = redis_client
        self.whatsapp_client = whatsapp_client
        self.queue = queue
        self.beneficiary_suggestion_service = beneficiary_suggestion_service
        self.mode = mode
        self.context_manager = context_manager

        self.user_repo = user_repo
        self.beneficiary_repo = beneficiary_repo
        self.account_repo = account_repo
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

    def _get_config(self, phone_number: str) -> RunnableConfig:
        """Create LangGraph configuration."""
        return {
            "configurable": {
                "thread_id": phone_number,
                "task_planner": self.task_planner,
                "services": self.services,
                "user_repo": self.user_repo,
                "beneficiary_repo": self.beneficiary_repo,
                "account_repo": self.account_repo,
                "banking_provider": self.banking_provider,
                "beneficiary_suggestion_service": self.beneficiary_suggestion_service,
                "redis_client": self.redis_client,
                "whatsapp_client": self.whatsapp_client,
                "queue": self.queue,
            },
            "recursion_limit": 50,
        }



    def _map_outbox_to_intents(self, outbox: list[dict[str, Any]], response_text: str | None) -> list[UiIntent]:
        """Convert raw outbox dicts to UiIntent objects."""
        intents: list[UiIntent] = []

        for item in outbox:
            msg_type = item.get("type")

            if msg_type == "say":
                intents.append(Say(text=item["text"]))

            elif msg_type == "auth_request":
                intents.append(
                    RequestAuth(
                        method=item.get("method", "pin"),
                        task_ids=item.get("task_ids", []),
                        correlation_id=item.get("idempotency_key", "unknown"),
                        reason=item.get("header"),
                        summary=item.get("summary"),
                    )
                )

            elif msg_type == "request_confirmation":
                intents.append(
                    RequestConfirmation(
                        task_ids=item.get("task_ids", []),
                        summary=item.get("summary", ""),
                        correlation_id=item.get("idempotency_key", "unknown"),
                        token=item.get("idempotency_key", "unknown"),
                    )
                )

            elif msg_type == "show_receipt":
                intents.append(
                    ShowReceipt(
                        task_id=item.get("task_id", "unknown"),
                        receipt=item.get("receipt", {}),
                        caption=item.get("caption", ""),
                    )
                )

            elif msg_type == "image" and "receipt" in item.get("caption", "").lower():
                intents.append(
                    ShowReceipt(task_id="unknown", receipt={"url": item["url"]}, caption=item.get("caption", ""))
                )
            elif msg_type == "flow":
                intents.append(
                    ShowFlow(
                        flow_id=item.get("flow_id", ""),
                        flow_config=item.get("flow_config", {}),
                        fallback_text=item.get("fallback_text", ""),
                    )
                )

        has_primary_interaction = any(isinstance(i, (RequestAuth, RequestConfirmation, ShowReceipt)) for i in intents)
        if response_text and not has_primary_interaction and not any(isinstance(i, Say) for i in intents):
            intents.append(Say(text=response_text))
            
        return intents

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
            "channel": context.channel,
        }

        # Hydrate via ContextManager (Parallel Fetch)
        user_ctx, _, _, _ = await self.context_manager.load_context_parallel(phone_number)

        loaded_context = {
            "profile": user_ctx.get("profile"),
            "accounts": user_ctx.get("accounts"),
            "beneficiaries": user_ctx.get("beneficiaries"),
            "language": user_ctx.get("language"),
            "user_id": user_ctx.get("profile", {}).get("id") if user_ctx.get("profile") else None,
        }

        inputs["loaded_context"] = loaded_context

        config = self._get_config(phone_number)

        logger.info("orchestrator_graph_invoke", user=phone_number)

        final_state = await self.graph.ainvoke(inputs, config=config)
        outbox = final_state.get("outbox", [])
        response_text = final_state.get("final_response")
        
        intents = self._map_outbox_to_intents(outbox, response_text)
        
        return {
            "text": response_text,
            "intents": intents,
            "outbox": outbox, # Keep raw outbox for logging/debug if needed
        }

    async def resume_flow(self, phone_number: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Resume flow externally (e.g. from auth callback)."""

        await self._ensure_checkpointer()
        inputs = {
            "user_id": phone_number,
            "phone_number": phone_number,
            "last_callback": payload,
        }

        config = self._get_config(phone_number)

        logger.info("orchestrator_graph_resume", user=phone_number, payload=payload)

        try:
            final_state = await self.graph.ainvoke(inputs, config=config)
            return {
                "text": final_state.get("final_response"),
                "outbox": final_state.get("outbox", []),
            }
        except Exception as e:
            logger.exception("graph_resume_error", error=str(e))
            return {"final_response": None, "outbox": []}
