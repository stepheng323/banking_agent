from langchain_openai import ChatOpenAI

from apps.core.src.agent.graphs.__shared__.beneficiary.suggestion_service import BeneficiarySuggestionService
from apps.core.src.agent.graphs.account.service import AccountService
from apps.core.src.agent.graphs.airtime import AirtimeService
from apps.core.src.agent.graphs.data.completion import DataCompletionService
from apps.core.src.agent.graphs.data.executor import DataExecutor
from apps.core.src.agent.graphs.data.service import DataService
from apps.core.src.agent.graphs.onboarding.executor import OnboardingExecutor
from apps.core.src.agent.graphs.onboarding.service import OnboardingService
from apps.core.src.agent.graphs.query import QueryService
from apps.core.src.agent.graphs.support import SupportService
from apps.core.src.agent.graphs.transfer import TransferService as AgentTransferService
from apps.core.src.agent.orchestrator import OrchestratorAgent
from apps.core.src.agent.orchestrator.config import OrchestratorDependencies
from apps.core.src.agent.orchestrator.services import MediaService
from apps.core.src.agent.shared.batch.service import BatchService
from apps.core.src.queue_consumers import MessageConsumer, TransactionConsumer
from apps.core.src.queue_consumers.flow_event_consumer import FlowEventConsumer
from shared.cache.bank_cache import BankCacheService
from shared.cache.flow_session_manager import FlowSessionManager
from shared.cache.redis_client import RedisClient
from shared.cache.user_data import UserDataCache
from shared.clients.factories.payment import PaymentProviderFactory
from shared.clients.providers.mono.banking import MonoBankingProvider
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config import settings
from shared.database.connection import get_db_session
from shared.queue.redis_queue import RedisQueue
from shared.repositories import AccountRepository, BeneficiaryRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.transaction_repository import TransactionRepository
from shared.repositories.user_repository import UserRepository
from shared.services import ConversationResponder
from shared.services.task_queue import TaskQueueService


def setup_dependencies() -> tuple[MessageConsumer, TransactionConsumer, FlowEventConsumer]:
    """Setup deps"""
    whatsapp_client = WhatsAppClient()
    redis_queue = RedisQueue(redis_url=settings.redis_url)
    user_repository = UserRepository(db=get_db_session())

    shared_redis = RedisClient.get_client()

    user_data_cache = UserDataCache(redis_client=shared_redis)

    onboarding_service = OnboardingService(whatsapp_client)
    onboarding_executor = OnboardingExecutor(whatsapp_client, user_repository, onboarding_service)

    beneficiary_repository = BeneficiaryRepository(db=get_db_session())
    account_repository = AccountRepository(db=get_db_session())
    actionable_message_repository = ActionableMessageRepository(db=get_db_session())

    beneficiary_suggestion_service = BeneficiarySuggestionService(
        whatsapp_client=whatsapp_client,
        redis_client=shared_redis,
    )

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    banking_provider = MonoBankingProvider()

    account_service = AccountService(
        account_repo=account_repository,
        user_repo=user_repository,
        llm=llm,
        messaging_client=whatsapp_client,
        banking_provider=banking_provider,
        session_manager=FlowSessionManager(),
    )

    bill_provider = PaymentProviderFactory.get_bill_payment_provider()
    data_service = None
    if bill_provider:
        data_service = DataService(
            bill_provider=bill_provider,
            redis_client=shared_redis,
            whatsapp_client=whatsapp_client,
            queue=redis_queue,
        )

    transaction_repository = TransactionRepository(db=get_db_session())
    support_service = SupportService(
        llm=llm,
        transaction_repo=transaction_repository,
        actionable_message_repo=actionable_message_repository,
        redis_client=shared_redis,
        db_session=get_db_session(),
    )

    query_service = QueryService(
        llm=llm,
        banking_provider=banking_provider,
        redis_client=shared_redis,
        user_cache=user_data_cache,
        support_service=support_service,
    )

    task_queue_service = TaskQueueService()
    conversation_responder = ConversationResponder(llm)

    bank_cache_service = BankCacheService(redis_client=shared_redis)

    agent_transfer_service = AgentTransferService(
        llm=llm,
        user_cache=user_data_cache,
        beneficiary_repo=beneficiary_repository,
        account_repo=account_repository,
        whatsapp_client=whatsapp_client,
        queue=redis_queue,
        actionable_message_repo=actionable_message_repository,
        completion_callback=None,
        user_repo=user_repository,
        banking_provider=banking_provider,
        bank_cache=bank_cache_service,
        transaction_repo=transaction_repository,
    )

    agent_airtime_service = AirtimeService(
        llm=llm,
        user_cache=user_data_cache,
        account_repo=account_repository,
        beneficiary_repo=beneficiary_repository,
        whatsapp_client=whatsapp_client,
        queue=redis_queue,
        actionable_message_repo=actionable_message_repository,
        completion_callback=None,
        transaction_repo=transaction_repository,
        banking_provider=banking_provider,
    )

    media_service = MediaService(whatsapp_client)

    orchestrator_deps = OrchestratorDependencies(
        llm=llm,
        user_repo=user_repository,
        beneficiary_repo=beneficiary_repository,
        actionable_message_repo=actionable_message_repository,
        whatsapp_client=whatsapp_client,
        task_queue_service=task_queue_service,
        conversation_responder=conversation_responder,
        transfer_service=agent_transfer_service,
        airtime_service=agent_airtime_service,
        query_service=query_service,
        account_service=account_service,
        media_service=media_service,
        data_service=data_service,
        user_cache=user_data_cache,
        account_repo=account_repository,
        redis_client=shared_redis,
        banking_provider=banking_provider,
        queue=redis_queue,
    )

    orchestrator = OrchestratorAgent(orchestrator_deps)

    batch_service = BatchService(
        whatsapp_client=whatsapp_client,
        task_queue_service=task_queue_service,
        transfer_service=agent_transfer_service,
        airtime_service=agent_airtime_service,
        data_service=data_service,
        query_service=query_service,
        user_cache=user_data_cache,
        account_service=account_service,
        queue=redis_queue,
    )

    message_consumer = MessageConsumer(
        redis_queue=redis_queue,
        user_repository=user_repository,
        onboarding_executor=onboarding_executor,
        orchestrator=orchestrator,
        messaging_client=whatsapp_client,
    )

    data_completion_service = DataCompletionService(
        whatsapp_client=whatsapp_client,
        redis_client=shared_redis,
        actionable_message_repo=actionable_message_repository,
        beneficiary_suggestion_service=beneficiary_suggestion_service,
    )

    data_executor = DataExecutor(data_service=data_completion_service)

    transaction_consumer = TransactionConsumer(
        redis_queue=redis_queue,
        transfer_executor=None,
        airtime_executor=None,
        data_executor=data_executor,
    )

    flow_event_consumer = FlowEventConsumer(
        redis_queue=redis_queue,
        transfer_service=agent_transfer_service,
        airtime_service=agent_airtime_service,
        data_service=data_service,
        batch_service=batch_service,
        whatsapp_client=whatsapp_client,
        orchestrator=orchestrator,
    )

    return message_consumer, transaction_consumer, flow_event_consumer
