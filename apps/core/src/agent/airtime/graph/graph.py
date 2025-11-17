"""LangGraph graph for airtime purchase flow."""

from typing import Optional
import asyncio

from langchain_core.runnables import RunnableConfig

from shared.cache.user_context_cache import UserContextCacheService
from shared.clients.whatsapp_client import WhatsAppClient
from shared.repositories.account_repository import AccountRepository
from apps.core.src.agent.services.airtime_entity_extractor import AirtimeEntityExtractor
from apps.core.src.agent.services.flow_completion_callback import FlowCompletionCallback

from .builder import build_graph
from .state import create_initial_state, update_conversation_state


class AirtimeFlowGraph:
    """LangGraph-based airtime purchase flow."""

    def __init__(
        self,
        user_cache: UserContextCacheService,
        account_repo: AccountRepository,
        whatsapp_client: WhatsAppClient,
        extractor: AirtimeEntityExtractor,
        completion_callback: Optional[FlowCompletionCallback] = None,
    ):
        self.user_cache = user_cache
        self.account_repo = account_repo
        self.whatsapp_client = whatsapp_client
        self.extractor = extractor
        self.completion_callback = completion_callback
        self.graph = None

    async def _ensure_graph(self):
        """Ensure graph is compiled."""
        if self.graph is None:
            workflow = build_graph()
            self.graph = workflow.compile()

    async def run(self, phone_number: str, message: str, message_id: str, classification_result: Optional[dict] = None) -> str:
        """Run the airtime purchase flow graph."""
        await self._ensure_graph()

        initial_state = create_initial_state(
            phone_number, message, message_id, classification_result)

        # TODO: Implement actual graph execution
        # config = RunnableConfig(
        #     configurable={
        #         "thread_id": phone_number,
        #     }
        # )
        result = initial_state
        await update_conversation_state(phone_number, result)

        # Call completion callback if flow completed or failed
        if self.completion_callback:
            airtime_status = result.get("airtime_status")
            if airtime_status in ("completed", "failed", "cancelled"):
                completion_result = {
                    "status": airtime_status,
                    "amount": result.get("amount"),
                    "recipient_phone": result.get("recipient_phone"),
                    "response": result.get("response", ""),
                }
                asyncio.create_task(
                    self.completion_callback.on_flow_complete(
                        phone_number, "airtime", completion_result
                    )
                )

        return result.get("response", "Airtime purchase flow is being set up. Please try again later.")
