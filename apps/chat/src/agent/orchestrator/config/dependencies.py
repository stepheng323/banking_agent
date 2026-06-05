"""Dependency container for the OrchestratorAgent."""

from dataclasses import dataclass

import redis.asyncio as redis
from langchain_openai import ChatOpenAI

from apps.chat.src.agent.orchestrator.conversation.conversation_responder import ConversationResponder
from apps.chat.src.agent.orchestrator.media.service import MediaService
from apps.chat.src.agent.orchestrator.task_state.service import TaskStateService
from banking.accounts.repositories.account_repository import AccountRepository
from banking.beneficiaries.repositories.beneficiary_repository import BeneficiaryRepository
from banking.beneficiaries.services.suggestion_service import BeneficiarySuggestionService
from banking.identity.repositories.user_repository import UserRepository
from banking.messaging.repositories.actionable_message_repository import ActionableMessageRepository
from banking.runtime.protocols import WorkerProtocol
from shared.cache.user_data import UserDataCache
from shared.clients.abstractions.banking import BankDataProvider
from shared.queue.adapter import QueuePublisher


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
    task_state_service: TaskStateService
    conversation_responder: ConversationResponder
    transfer_service: WorkerProtocol
    airtime_service: WorkerProtocol
    query_service: WorkerProtocol
    account_service: WorkerProtocol
    beneficiary_service: WorkerProtocol
    media_service: MediaService
    data_service: WorkerProtocol
    support_service: WorkerProtocol
    faq_service: WorkerProtocol
    publisher: QueuePublisher
    banking_provider: BankDataProvider
    beneficiary_suggestion_service: BeneficiarySuggestionService
    user_cache: UserDataCache
    redis_client: redis.Redis
