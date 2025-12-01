"""Transfer service facade using LangGraph."""

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from apps.core.src.agent.services.flow_completion_callback import FlowCompletionCallback

from langchain_openai import ChatOpenAI

from shared.cache.user_context_cache import UserContextCacheService
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.account_repository import AccountRepository
from shared.clients.whatsapp_client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue

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
        queue: RedisQueue,
        completion_callback: Optional["FlowCompletionCallback"] = None,
    ) -> None:
        self.extractor = TransferEntityExtractor(llm)
        self.graph = TransferFlowGraph(
            user_cache=user_cache,
            beneficiary_repo=beneficiary_repo,
            account_repo=account_repo,
            whatsapp_client=whatsapp_client,
            extractor=self.extractor,
            queue=queue,
            completion_callback=completion_callback,
        )

    async def run_simple(self, phone: str, text: str, classification_result: Optional[dict] = None) -> str:
        """Run the transfer flow using LangGraph."""
        print(f"🔍 [TRANSFER_SERVICE] run_simple called with message: '{text}'")
        return await self.graph.run(phone, text, "", classification_result)
    
    async def clear_checkpoint(self, phone_number: str) -> None:
        """Clear transfer flow checkpoint for a user."""
        try:
            await self.graph.clear_checkpoint(phone_number)
        except Exception as e:
            print(f"⚠️  Error clearing transfer checkpoint: {e}")