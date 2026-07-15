"""Domain service construction for the chat runtime."""

from collections.abc import Callable
from dataclasses import dataclass

import redis.asyncio as redis
from langchain_openai import ChatOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from apps.chat.src.agent.orchestrator.conversation.conversation_responder import ConversationResponder
from apps.chat.src.agent.orchestrator.media.service import MediaService
from apps.chat.src.agent.orchestrator.task_state.service import TaskStateService
from apps.chat.src.runtime.providers import ChatRuntimeProviders
from apps.chat.src.runtime.repositories import ChatRuntimeRepositories
from banking.accounts.onboarding.executor import OnboardingExecutor
from banking.accounts.onboarding.runtime import session_manager as onboarding_session_manager
from banking.accounts.onboarding.service import OnboardingService
from banking.accounts.runtime import build_account_worker
from banking.beneficiaries.runtime import build_beneficiary_worker
from banking.beneficiaries.services.suggestion_service import BeneficiarySuggestionService
from banking.bills.airtime.runtime import build_airtime_worker
from banking.bills.data.runtime import build_data_worker
from banking.faq.runtime import build_faq_worker
from banking.runtime.protocols import WorkerProtocol
from banking.support.runtime import build_support_worker
from banking.support.services.ticket_service import TicketService
from banking.transactions.query.runtime import build_query_worker
from banking.transfers.runtime import build_transfer_worker
from shared.cache.bank_cache import BankCacheService
from shared.cache.user_data import UserDataCache
from shared.clients.abstractions.messaging import MessagingClient
from shared.queue.adapter import QueuePublisher


@dataclass(slots=True)
class ChatDomainServices:
    """Domain workers and supporting services for one chat runtime bundle."""

    user_data_cache: UserDataCache
    onboarding_executor: OnboardingExecutor
    beneficiary_suggestion_service: BeneficiarySuggestionService
    task_state_service: TaskStateService
    conversation_responder: ConversationResponder
    media_service: MediaService
    account_worker: WorkerProtocol
    beneficiary_worker: WorkerProtocol
    data_worker: WorkerProtocol
    support_worker: WorkerProtocol
    query_worker: WorkerProtocol
    airtime_worker: WorkerProtocol
    faq_worker: WorkerProtocol
    transfer_worker: WorkerProtocol


def build_chat_domain_services(
    *,
    repositories: ChatRuntimeRepositories,
    providers: ChatRuntimeProviders,
    queue_publisher: QueuePublisher,
    messaging_clients: dict[str, MessagingClient],
    shared_redis: redis.Redis,
    llm: ChatOpenAI,
    conversation_llm: ChatOpenAI,
    query_llm: ChatOpenAI,
    extractor_llm: ChatOpenAI,
    session_factory: Callable[[], AsyncSession],
) -> ChatDomainServices:
    """Build domain workers and chat orchestration support services."""
    user_data_cache = UserDataCache(redis_client=shared_redis)
    onboarding_service = OnboardingService(queue_publisher)
    onboarding_executor = OnboardingExecutor(repositories.user, onboarding_service)
    beneficiary_suggestion_service = BeneficiarySuggestionService(queue_publisher)

    account_worker = build_account_worker(
        account_repo=repositories.account,
        user_repo=repositories.user,
        llm=llm,
        banking_provider=providers.bank_data_provider,
        session_manager=onboarding_session_manager,
        direct_debit_provider=providers.direct_debit_provider,
    )
    beneficiary_worker = build_beneficiary_worker()
    data_worker = build_data_worker(
        extractor_llm=extractor_llm,
        bill_provider=providers.bill_provider,
        transaction_repo=repositories.transaction,
        publisher=queue_publisher,
        redis_client=shared_redis,
    )
    support_worker = build_support_worker(
        llm=llm,
        transaction_repo=repositories.transaction,
        actionable_message_repo=repositories.actionable_message,
        bank_transaction_repo=repositories.bank_transaction,
        redis_client=shared_redis,
        ticket_service=TicketService(session_factory=session_factory),
    )
    query_worker = build_query_worker(
        llm=query_llm,
        banking_provider=providers.bank_data_provider,
        redis_client=shared_redis,
    )

    task_state_service = TaskStateService()
    conversation_responder = ConversationResponder(conversation_llm)
    bank_cache_service = BankCacheService(
        redis_client=shared_redis, provider_name=providers.resolver_provider.provider_name
    )
    payout_bank_cache_service = BankCacheService(
        redis_client=shared_redis,
        provider_name=providers.payout_resolver_provider.provider_name,
    )

    airtime_worker = build_airtime_worker(
        extractor_llm=extractor_llm,
        bill_provider=providers.bill_provider,
        transaction_repo=repositories.transaction,
        publisher=queue_publisher,
        redis_client=shared_redis,
    )
    faq_worker = build_faq_worker(
        llm=llm,
        get_db=session_factory,
    )
    transfer_worker = build_transfer_worker(
        extractor_llm=extractor_llm,
        validation_service=None,
        publisher=queue_publisher,
        resolver_provider=providers.resolver_provider,
        bank_cache=bank_cache_service,
        transaction_repo=repositories.transaction,
        payout_resolver_provider=providers.payout_resolver_provider,
        payout_bank_cache=payout_bank_cache_service,
        dd_provider=providers.direct_debit_provider,
        redis_client=shared_redis,
    )
    media_service = MediaService(messaging_clients)

    return ChatDomainServices(
        user_data_cache=user_data_cache,
        onboarding_executor=onboarding_executor,
        beneficiary_suggestion_service=beneficiary_suggestion_service,
        task_state_service=task_state_service,
        conversation_responder=conversation_responder,
        media_service=media_service,
        account_worker=account_worker,
        beneficiary_worker=beneficiary_worker,
        data_worker=data_worker,
        support_worker=support_worker,
        query_worker=query_worker,
        airtime_worker=airtime_worker,
        faq_worker=faq_worker,
        transfer_worker=transfer_worker,
    )
