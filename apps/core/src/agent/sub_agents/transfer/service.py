"""Transfer service facade using LangGraph."""

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from apps.core.src.agent.tools.flow_completion import FlowCompletionCallback

from langchain_openai import ChatOpenAI

from shared.cache.user_data import UserDataCache
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.account_repository import AccountRepository
from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue
from shared.utils.logging import get_logger

from apps.core.src.agent.sub_agents.transfer.extractor import TransferEntityExtractor
from apps.core.src.agent.sub_agents.transfer.graph import TransferFlowGraph

logger = get_logger(__name__)


class TransferService:
    """Transfer service facade using LangGraph."""

    def __init__(
        self,
        llm: Optional[ChatOpenAI],
        user_cache: UserDataCache,
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

    async def run_simple(
        self, 
        phone: str, 
        text: str, 
        classification_result: Optional[dict] = None,
        image_data: str | None = None
    ) -> str:
        """Run the transfer flow using LangGraph."""
        logger.debug("transfer_flow_started", phone=phone, message=text[:100])
        return await self.graph.run(phone, text, "", classification_result, image_data=image_data)
    
    async def clear_checkpoint(self, phone_number: str) -> None:
        """Clear transfer flow checkpoint for a user."""
        try:
            await self.graph.clear_checkpoint(phone_number)
        except Exception as e:
            logger.error("transfer_checkpoint_clear_error", phone=phone_number, error=str(e), exc_info=True)