"""LangGraph graph for airtime purchase flow."""

import os
from typing import Optional, cast
import asyncio

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from shared.cache.user_context_cache import UserContextCacheService
from shared.clients.whatsapp_client import WhatsAppClient
from shared.repositories.account_repository import AccountRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.cache.redis_client import RedisClient
from apps.core.src.agent.airtime.extractor import AirtimeEntityExtractor
from apps.core.src.agent.services import BeneficiaryMatcher, FlowCompletionCallback
from apps.core.src.agent.airtime.state import AirtimeState

from .builder import build_graph
from .state import create_initial_state, update_conversation_state


class AirtimeFlowGraph:
    """LangGraph-based airtime purchase flow."""

    def __init__(
        self,
        user_cache: UserContextCacheService,
        account_repo: AccountRepository,
        beneficiary_repo: BeneficiaryRepository,
        whatsapp_client: WhatsAppClient,
        extractor: AirtimeEntityExtractor,
        completion_callback: Optional[FlowCompletionCallback] = None,
    ):
        self.user_cache = user_cache
        self.account_repo = account_repo
        self.beneficiary_repo = beneficiary_repo
        self.whatsapp_client = whatsapp_client
        self.extractor = extractor
        self.matcher = BeneficiaryMatcher()
        self.completion_callback = completion_callback
        self.redis_client = RedisClient.get_client()

        self.graph = None
        self._checkpointer_cm = None
        self._checkpointer = None
        self._checkpointer_setup = False

    async def _ensure_checkpointer(self):
        """Ensure checkpointer is initialized and graph is compiled."""
        if not self._checkpointer_setup:
            db_url = os.getenv("DATABASE_URL", "")
            if not db_url:
                raise ValueError("DATABASE_URL required for checkpointing")
            self._checkpointer_cm = AsyncPostgresSaver.from_conn_string(db_url)
            self._checkpointer = await self._checkpointer_cm.__aenter__()
            self._checkpointer_setup = True

        if self.graph is None:
            self.graph = build_graph(
                extractor=self.extractor,
                user_cache=self.user_cache,
                account_repo=self.account_repo,
                beneficiary_repo=self.beneficiary_repo,
                matcher=self.matcher,
                whatsapp_client=self.whatsapp_client,
                redis_client=self.redis_client,
            ).compile(checkpointer=self._checkpointer)

    async def run(self, phone_number: str, message: str, message_id: str, classification_result: Optional[dict] = None) -> str:
        """Run the airtime purchase flow graph."""
        await self._ensure_checkpointer()

        config: RunnableConfig = {
            "configurable": {
                "thread_id": f"airtime:{phone_number}",
            }
        }

        if self.graph is None:
            raise RuntimeError("Graph not compiled")

        input_state = create_initial_state(
            phone_number, message, message_id, classification_result)
        final_state = await self.graph.ainvoke(cast(AirtimeState, input_state), config)
        await update_conversation_state(phone_number, cast(AirtimeState, final_state))

        # Call completion callback if flow completed or failed
        if self.completion_callback:
            airtime_status = final_state.get("airtime_status")
            if airtime_status in ("completed", "failed", "cancelled"):
                completion_result = {
                    "status": airtime_status,
                    "amount": final_state.get("amount"),
                    "recipient_phone": final_state.get("recipient_phone"),
                    "response": final_state.get("response", ""),
                }
                asyncio.create_task(
                    self.completion_callback.on_flow_complete(
                        phone_number, "airtime", completion_result
                    )
                )

        return final_state.get("response", "")
