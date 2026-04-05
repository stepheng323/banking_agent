"""Configuration and dependencies for the OrchestratorAgent."""

from dataclasses import dataclass

import redis.asyncio as redis
from langchain_openai import ChatOpenAI

from apps.core.src.agent.graphs.__shared__.beneficiary.suggestion_service import BeneficiarySuggestionService
from apps.core.src.agent.orchestrator.services.media_service import MediaService
from shared.cache.user_data import UserDataCache
from shared.clients.abstractions.banking import BankDataProvider
from shared.protocols.worker import WorkerProtocol
from shared.queue.adapter import QueuePublisher
from shared.repositories.account_repository import AccountRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.user_repository import UserRepository
from shared.services.conversation_responder import ConversationResponder
from shared.services.task_queue.service import TaskQueueService


@dataclass
class OrchestratorDependencies:
    """Dependencies required by the OrchestratorAgent."""

    llm: ChatOpenAI
    semantic_router_llm: ChatOpenAI | None
    interrupt_llm: ChatOpenAI | None
    user_repo: UserRepository
    account_repo: AccountRepository
    beneficiary_repo: BeneficiaryRepository
    actionable_message_repo: ActionableMessageRepository
    task_queue_service: TaskQueueService
    conversation_responder: ConversationResponder
    transfer_service: WorkerProtocol
    airtime_service: WorkerProtocol
    query_service: WorkerProtocol
    account_service: WorkerProtocol
    media_service: MediaService
    data_service: WorkerProtocol
    support_service: WorkerProtocol
    faq_service: WorkerProtocol
    publisher: QueuePublisher
    banking_provider: BankDataProvider
    beneficiary_suggestion_service: BeneficiarySuggestionService
    user_cache: UserDataCache
    redis_client: redis.Redis
