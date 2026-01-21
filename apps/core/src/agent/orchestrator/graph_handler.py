"""Orchestrator Graph Handler.

Integrates the Top-Level LangGraph into the Message Processing Pipeline.
"""

from typing import Any, Literal

import redis.asyncio as redis
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph.state import CompiledStateGraph

from apps.core.src.agent.graphs.__shared__.beneficiary.suggestion_service import BeneficiarySuggestionService
from apps.core.src.agent.orchestrator.factory import AdapterFactory
from apps.core.src.agent.orchestrator.graph import build_orchestrator_graph
from apps.core.src.agent.orchestrator.models.message_context import MessageContext
from shared.cache.user_data import UserDataCache
from shared.clients.abstractions.banking import BankingDataProvider
from shared.clients.whatsapp.client import WhatsAppClient
from shared.protocols.services import (
    AccountManagementServiceProtocol,
    AirtimeServiceProtocol,
    DataServiceProtocol,
    FAQServiceProtocol,
    QueryServiceProtocol,
    SupportServiceProtocol,
    TransferServiceProtocol,
)
from shared.repositories.account_repository import AccountRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.user_repository import UserRepository
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
        transfer_service: TransferServiceProtocol,
        airtime_service: AirtimeServiceProtocol,
        query_service: QueryServiceProtocol,
        data_service: DataServiceProtocol,
        account_management_service: AccountManagementServiceProtocol,
        support_service: SupportServiceProtocol,
        faq_service: FAQServiceProtocol,
        user_repo: UserRepository,
        beneficiary_repo: BeneficiaryRepository,
        account_repo: AccountRepository,
        banking_provider: BankingDataProvider,
        user_cache: UserDataCache,
        redis_client: redis.Redis,
        whatsapp_client: WhatsAppClient,
        beneficiary_suggestion_service: BeneficiarySuggestionService | None = None,
        mode: Literal["planning", "execution", "both"] = "both",
    ):
        self.task_planner = task_planner
        self.redis_client = redis_client
        self.whatsapp_client = whatsapp_client
        self.beneficiary_suggestion_service = beneficiary_suggestion_service
        self.mode = mode

        self.user_repo = user_repo
        self.beneficiary_repo = beneficiary_repo
        self.account_repo = account_repo
        self.banking_provider = banking_provider

        # optimize services dict
        self.services = {
            "transfer": transfer_service,
            "airtime": airtime_service,
            "query": query_service,
            "data": data_service,
            "manage_accounts": account_management_service,
            "support": support_service,
            "faq": faq_service,
        }

        self.adapter_factory = AdapterFactory(self.services, user_cache)

        self.checkpointer = AsyncRedisSaver(redis_client=redis_client)
        self._checkpointer_setup = False

        self.graph: CompiledStateGraph = build_orchestrator_graph(checkpointer=self.checkpointer)

    async def _ensure_checkpointer(self) -> None:
        """Ensure checkpointer is initialized."""
        if not self._checkpointer_setup:
            await self.checkpointer.asetup()
            self._checkpointer_setup = True

    async def invoke(self, context: MessageContext) -> str | None:
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
        }

        # Config
        config: RunnableConfig = {
            "configurable": {
                "thread_id": phone_number,
                "task_planner": self.task_planner,
                "adapter_factory": self.adapter_factory,
                "whatsapp_client": self.whatsapp_client,
                "services": self.services,
                "user_repo": self.user_repo,
                "beneficiary_repo": self.beneficiary_repo,
                "account_repo": self.account_repo,
                "banking_provider": self.banking_provider,
                "beneficiary_suggestion_service": self.beneficiary_suggestion_service,
            },
            "recursion_limit": 50,
        }

        logger.info("orchestrator_graph_invoke", user=phone_number)

        # Invoke Graph
        final_state = await self.graph.ainvoke(inputs, config=config)

        # Process Output
        return {
            "final_response": final_state.get("final_response"),
            "outbox": final_state.get("outbox", []),
        }

    async def resume_flow(self, phone_number: str, payload: dict[str, Any]) -> str | None:
        """Resume flow externally (e.g. from auth callback)."""

        await self._ensure_checkpointer()
        inputs = {
            "user_id": phone_number,
            "phone_number": phone_number,
            "last_callback": payload,
        }

        config: RunnableConfig = {
            "configurable": {
                "thread_id": phone_number,
                "task_planner": self.task_planner,
                "adapter_factory": self.adapter_factory,
                "whatsapp_client": self.whatsapp_client,
                "services": self.services,
                "user_repo": self.user_repo,
                "beneficiary_repo": self.beneficiary_repo,
                "account_repo": self.account_repo,
                "banking_provider": self.banking_provider,
                "beneficiary_suggestion_service": self.beneficiary_suggestion_service,
            },
            "recursion_limit": 50,
        }

        logger.info("orchestrator_graph_resume", user=phone_number, payload=payload)

        try:
            final_state = await self.graph.ainvoke(inputs, config=config)
            return final_state.get("final_response")
        except Exception as e:
            logger.exception("graph_resume_error", error=str(e))
            return None
