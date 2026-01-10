"""Transfer service facade using LangGraph."""

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.services.task_coordinator import TaskCoordinator

from langchain_openai import ChatOpenAI

from apps.core.src.agent.graphs.transfer.extractor import TransferEntityExtractor
from apps.core.src.agent.graphs.transfer.graph import TransferFlowGraph
from shared.cache.user_data import UserDataCache
from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue
from shared.repositories.account_repository import AccountRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.user_repository import UserRepository
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransferService:
    """Transfer service facade using LangGraph."""

    def __init__(
        self,
        llm: ChatOpenAI | None,
        user_cache: UserDataCache,
        beneficiary_repo: BeneficiaryRepository,
        account_repo: AccountRepository,
        whatsapp_client: WhatsAppClient,
        queue: RedisQueue,
        actionable_message_repo: ActionableMessageRepository | None = None,
        completion_callback: Optional["TaskCoordinator"] = None,
        user_repo: UserRepository | None = None,
    ) -> None:
        self.extractor = TransferEntityExtractor(llm)
        self.graph = TransferFlowGraph(
            user_cache=user_cache,
            beneficiary_repo=beneficiary_repo,
            account_repo=account_repo,
            whatsapp_client=whatsapp_client,
            extractor=self.extractor,
            queue=queue,
            actionable_message_repo=actionable_message_repo,
            completion_callback=completion_callback,
            user_repo=user_repo,
        )

    async def run_simple(
        self,
        phone: str,
        text: str,
        classification_result: dict | None = None,
        image_data: str | None = None,
        quoted_data: dict | None = None,
    ) -> str:
        """Run the transfer flow using LangGraph."""
        logger.debug("transfer_flow_started", phone=phone, message=text[:100])
        return await self.graph.run(
            phone, text, "", classification_result, image_data=image_data, quoted_data=quoted_data
        )

    async def clear_checkpoint(self, phone_number: str) -> None:
        """Clear transfer flow checkpoint for a user."""
        try:
            await self.graph.clear_checkpoint(phone_number)
        except Exception as e:
            logger.error("transfer_checkpoint_clear_error", phone=phone_number, error=str(e), exc_info=True)
