"""Airtime purchase service facade using LangGraph."""

from typing import Optional

from langchain_openai import ChatOpenAI

from shared.cache.user_context_cache import UserContextCacheService
from shared.repositories.account_repository import AccountRepository
from shared.clients.whatsapp_client import WhatsAppClient

from apps.core.src.agent.services.airtime_entity_extractor import AirtimeEntityExtractor
from apps.core.src.agent.services.flow_completion_callback import FlowCompletionCallback
from apps.core.src.agent.airtime.graph import AirtimeFlowGraph


class AirtimeService:
    """Airtime purchase service facade using LangGraph."""

    def __init__(
        self,
        llm: Optional[ChatOpenAI],
        user_cache: UserContextCacheService,
        account_repo: AccountRepository,
        whatsapp_client: WhatsAppClient,
        completion_callback: Optional[FlowCompletionCallback] = None,
    ) -> None:
        self.extractor = AirtimeEntityExtractor(llm)
        self.graph = AirtimeFlowGraph(
            user_cache=user_cache,
            account_repo=account_repo,
            whatsapp_client=whatsapp_client,
            extractor=self.extractor,
            completion_callback=completion_callback,
        )

    async def run_simple(self, phone: str, text: str, classification_result: Optional[dict] = None) -> str:
        """Run the airtime purchase flow using LangGraph."""
        return await self.graph.run(phone, text, "", classification_result)
