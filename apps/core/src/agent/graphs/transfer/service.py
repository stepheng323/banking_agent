"""Transfer service facade."""

from typing import Any

from langchain_openai import ChatOpenAI

from apps.core.src.agent.graphs.transfer.services.extractor import TransferEntityExtractor
from apps.core.src.agent.graphs.transfer.worker import TransferWorker
from shared.cache.user_data import UserDataCache
from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue
from shared.repositories.account_repository import AccountRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.transaction_repository import TransactionRepository
from shared.repositories.user_repository import UserRepository
from shared.utils.logging import get_logger

from ..interfaces import FlowCompletionCallback, IAgentService

logger = get_logger(__name__)


class TransferService(IAgentService):
    """
    Transfer Service Container.

    This service acts as a dependency injection container for the TransferWorker.
    It implements IAgentService for compatibility but delegates all logic to the V3 Orchestrator.
    """

    def __init__(
        self,
        llm: ChatOpenAI | None,
        user_cache: UserDataCache,
        beneficiary_repo: BeneficiaryRepository,
        account_repo: AccountRepository,
        whatsapp_client: WhatsAppClient,
        queue: RedisQueue,
        actionable_message_repo: ActionableMessageRepository | None = None,
        completion_callback: FlowCompletionCallback | None = None,
        user_repo: UserRepository | None = None,
        banking_provider: Any | None = None,
        bank_cache: Any | None = None,
        transaction_repo: TransactionRepository | None = None,
    ) -> None:
        self.extractor = TransferEntityExtractor(llm)

        self.worker = TransferWorker(
            validation_service=None,
            beneficiary_repo=beneficiary_repo,
            account_repo=account_repo,
            queue=queue,
            extractor=self.extractor,
            banking_provider=banking_provider,
            bank_cache=bank_cache,
            transaction_repo=transaction_repo,
        )

    async def run_simple(
        self,
        phone: str,
        text: str,
        classification_result: dict | None = None,
        image_data: str | None = None,
        quoted_data: dict | None = None,
    ) -> str:
        """Deprecated: Use Orchestrator."""
        logger.warning("legacy_run_simple_called", phone=phone)
        return "Transfer service has been migrated to V3 Orchestrator."

    async def preflight(self, phone: str, text: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """No-op: Preflight is handled dynamically by the Worker."""
        return {"ready": True, "enriched_params": params or {}}

    async def clear_checkpoint(self, phone_number: str) -> None:
        """No-op: Checkpoints are managed by the Orchestrator."""
        pass

    async def resume_after_pin_verification(
        self, phone_number: str, pin_verified: bool, extra_param: Any = None
    ) -> str:
        """No-op: Orchestrator handles thread resumption."""
        return "Resumed"

    async def get_last_state(self, phone: str) -> dict[str, Any] | None:
        """No-op: State is managed by Orchestrator."""
        return None

    def set_completion_callback(self, callback: FlowCompletionCallback | None) -> None:
        """No-op: Callbacks handled via shared infrastructure."""
        pass
