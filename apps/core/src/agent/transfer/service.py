"""Transfer service facade using LangGraph."""

from typing import Optional

from langchain_openai import ChatOpenAI

from shared.cache.user_context_cache import UserContextCacheService
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.account_repository import AccountRepository
from shared.clients.whatsapp_client import WhatsAppClient

from apps.core.src.agent.transfer.extractor import TransferEntityExtractor
from apps.core.src.agent.transfer.graph import TransferFlowGraph


class TransferService:
    """Transfer service facade using LangGraph."""
    def __init__(
        self,
        llm: Optional[ChatOpenAI],
        user_cache: UserContextCacheService,
        beneficiary_repo: BeneficiaryRepository,
        account_repo: AccountRepository,
        whatsapp_client: WhatsAppClient,
    ) -> None:
        self.extractor = TransferEntityExtractor(llm)
        self.graph = TransferFlowGraph(
            user_cache=user_cache,
            beneficiary_repo=beneficiary_repo,
            account_repo=account_repo,
            whatsapp_client=whatsapp_client,
            extractor=self.extractor,
        )

    async def run_simple(self, phone: str, text: str) -> str:
        """Run the transfer flow using LangGraph."""
        return await self.graph.run(phone, text, "")
