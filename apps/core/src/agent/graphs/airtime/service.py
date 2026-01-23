"""Airtime purchase service facade."""

from typing import Any

from langchain_openai import ChatOpenAI

from apps.core.src.agent.graphs.airtime.extractor import AirtimeEntityExtractor

from apps.core.src.agent.graphs.airtime.worker import AirtimeWorker
from shared.cache.bank_cache import BankCacheService
from shared.cache.user_data import UserDataCache
from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue
from shared.repositories.account_repository import AccountRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.transaction_repository import TransactionRepository
from apps.core.src.agent.graphs.__shared__.validation.service import AsyncValidationService
from shared.utils.logging import get_logger

from ..interfaces import FlowCompletionCallback, IAgentService

logger = get_logger(__name__)


class AirtimeService(IAgentService):
    """Airtime purchase service facade."""

    def __init__(
        self,
        llm: ChatOpenAI | None,
        user_cache: UserDataCache,
        account_repo: AccountRepository,
        beneficiary_repo: BeneficiaryRepository,
        whatsapp_client: WhatsAppClient,
        queue: RedisQueue,
        actionable_message_repo: ActionableMessageRepository | None = None,
        completion_callback: FlowCompletionCallback | None = None,
        transaction_repo: TransactionRepository | None = None,
        banking_provider: Any | None = None,
    ) -> None:
        self.extractor = AirtimeEntityExtractor(llm)
        self.worker = AirtimeWorker(
            extractor=self.extractor,
            banking_provider=banking_provider,
            validation_service=AsyncValidationService(banking_provider),
            transaction_repo=transaction_repo,
            queue=queue,
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
        return "Airtime service has been migrated to V3 Orchestrator."

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

    async def preflight(self, phone: str, text: str, params: dict[str, Any]) -> dict[str, Any]:
        """No-op: Preflight is handled dynamically by the Worker."""
        return {"ready": True, "enriched_params": {}}

    def set_completion_callback(self, callback: FlowCompletionCallback | None) -> None:
        """No-op: Callbacks handled via shared infrastructure."""
        pass

