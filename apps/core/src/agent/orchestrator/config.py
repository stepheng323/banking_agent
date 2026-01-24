"""Configuration and dependencies for the OrchestratorAgent."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import redis.asyncio as redis
from langchain_openai import ChatOpenAI

from shared.cache.user_data import UserDataCache
from shared.clients.abstractions.banking import BankingDataProvider
from shared.clients.whatsapp.client import WhatsAppClient
from shared.protocols.worker import WorkerProtocol
from shared.repositories import BeneficiaryRepository, UserRepository
from shared.repositories.account_repository import AccountRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.services import ConversationResponder
from shared.services.task_queue import TaskQueueService

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.__shared__.beneficiary.suggestion_service import BeneficiarySuggestionService
    from shared.queue.redis_queue import RedisQueue


@dataclass
class OrchestratorDependencies:
    """Dependencies required by the OrchestratorAgent."""

    llm: ChatOpenAI
    user_repo: UserRepository
    account_repo: AccountRepository
    beneficiary_repo: BeneficiaryRepository
    actionable_message_repo: ActionableMessageRepository
    whatsapp_client: WhatsAppClient
    task_queue_service: TaskQueueService
    conversation_responder: ConversationResponder
    transfer_service: WorkerProtocol
    airtime_service: WorkerProtocol
    query_service: WorkerProtocol
    account_service: WorkerProtocol
    media_service: Any | None = None
    data_service: WorkerProtocol | None = None
    support_service: Any | None = None
    faq_service: Any | None = None
    banking_provider: BankingDataProvider | None = None
    beneficiary_suggestion_service: "BeneficiarySuggestionService | None" = None
    user_cache: UserDataCache | None = None
    redis_client: redis.Redis | None = None
    queue: "RedisQueue | None" = None
