"""Airtime purchase service facade using LangGraph."""

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

from apps.core.src.agent.sub_agents.airtime.extractor import AirtimeEntityExtractor
from apps.core.src.agent.sub_agents.airtime.graph import AirtimeFlowGraph

logger = get_logger(__name__)


class AirtimeService:
    """Airtime purchase service facade using LangGraph."""

    def __init__(
        self,
        llm: Optional[ChatOpenAI],
        user_cache: UserDataCache,
        account_repo: AccountRepository,
        beneficiary_repo: BeneficiaryRepository,
        whatsapp_client: WhatsAppClient,
        queue: RedisQueue,
        completion_callback: Optional["FlowCompletionCallback"] = None,
    ) -> None:
        self.extractor = AirtimeEntityExtractor(llm)
        self.graph = AirtimeFlowGraph(
            user_cache=user_cache,
            account_repo=account_repo,
            beneficiary_repo=beneficiary_repo,
            whatsapp_client=whatsapp_client,
            extractor=self.extractor,
            queue=queue,
            completion_callback=completion_callback,
        )

    async def run_simple(self, phone: str, text: str, classification_result: Optional[dict] = None) -> str:
        """Run the airtime purchase flow using LangGraph."""
        return await self.graph.run(phone, text, "", classification_result)

    async def clear_checkpoint(self, phone_number: str) -> None:
        """Clear airtime flow checkpoint for a user."""
        try:
            await self.graph.clear_checkpoint(phone_number)
        except Exception as e:
            logger.error("airtime_checkpoint_clear_error", phone=phone_number, error=str(e), exc_info=True)
