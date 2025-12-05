"""LangGraph graph for airtime purchase flow."""

import os
from typing import Optional, cast, TYPE_CHECKING

if TYPE_CHECKING:
    from apps.core.src.agent.services import FlowCompletionCallback
import asyncio

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from apps.core.src.agent.tools.cache.user_data import UserDataCache
from shared.clients.whatsapp_client import WhatsAppClient
from shared.repositories.account_repository import AccountRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.cache.redis_client import RedisClient
from shared.queue.redis_queue import RedisQueue
from shared.config.settings import settings
from apps.core.src.agent.sub_agents.airtime.extractor import AirtimeEntityExtractor
from apps.core.src.agent.tools.beneficiary.matcher import BeneficiaryMatcher
from apps.core.src.agent.sub_agents.airtime.state import AirtimeState

from .builder import build_graph
from .state import create_initial_state, update_conversation_state


class AirtimeFlowGraph:
    """LangGraph-based airtime purchase flow."""

    def __init__(
        self,
        user_cache: UserDataCache,
        account_repo: AccountRepository,
        beneficiary_repo: BeneficiaryRepository,
        whatsapp_client: WhatsAppClient,
        extractor: AirtimeEntityExtractor,
        queue: RedisQueue,
        completion_callback: Optional["FlowCompletionCallback"] = None,
    ):
        self.user_cache = user_cache
        self.account_repo = account_repo
        self.beneficiary_repo = beneficiary_repo
        self.whatsapp_client = whatsapp_client
        self.extractor = extractor
        self.matcher = BeneficiaryMatcher()
        self.completion_callback = completion_callback
        self.redis_client = RedisClient.get_client()
        self.queue = queue

        self.graph = None
        self._checkpointer_cm = None
        self._checkpointer = None
        self._checkpointer_setup = False

    async def _ensure_checkpointer(self):
        """Ensure checkpointer is initialized and graph is compiled."""
        if not self._checkpointer_setup:
            # Use Redis Stack checkpointer (<1ms latency, includes RediSearch module)
            self._checkpointer = AsyncRedisSaver(redis_url=settings.redis_url)
            await self._checkpointer.asetup()
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
                queue=self.queue,
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

    async def resume_after_pin_verification(
        self, phone_number: str, pin_verified: bool, pin_error: Optional[str] = None
    ) -> str:
        """
        Resume graph execution after PIN verification.

        Args:
            phone_number: User's phone number
            pin_verified: Whether PIN was verified successfully
            pin_error: Error message if PIN verification failed

        Returns:
            Response message
        """
        await self._ensure_checkpointer()

        config: RunnableConfig = {
            "configurable": {
                "thread_id": f"airtime:{phone_number}",
            }
        }

        if self.graph is None:
            raise RuntimeError("Graph not compiled")

        current_state = await self.graph.aget_state(config)
        if not current_state or not current_state.values:
            return "No active airtime purchase session found."

        updated_state = dict(current_state.values)
        updated_state.update({
            "phone_number": phone_number,
            "message": "",  
            "message_id": "",
            "pin_verified": pin_verified,
            "pin_verification_error": pin_error,
            "flow_state": "authorizing",
            "airtime_status": "pending",  # Clear collection_complete status
        })

        final_state = await self.graph.ainvoke(cast(AirtimeState, updated_state), config)
        await update_conversation_state(phone_number, cast(AirtimeState, final_state))

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
