from langchain_openai import ChatOpenAI

from apps.core.src.agent.executors.airtime import AirtimeExecutor
from apps.core.src.agent.executors.data import DataExecutor
from apps.core.src.agent.executors.transfer import TransferExecutor
from apps.core.src.agent.graphs.__shared__.beneficiary.suggestion_service import BeneficiarySuggestionService
from apps.core.src.agent.graphs.account.worker import AccountWorker
from apps.core.src.agent.graphs.airtime.extractor import AirtimeEntityExtractor
from apps.core.src.agent.graphs.airtime.worker import AirtimeWorker
from apps.core.src.agent.graphs.data.extractor import DataEntityExtractor
from apps.core.src.agent.graphs.data.worker import DataWorker
from apps.core.src.agent.graphs.faq.worker import FAQWorker
from apps.core.src.agent.graphs.onboarding.executor import OnboardingExecutor
from apps.core.src.agent.graphs.onboarding.service import OnboardingService
from apps.core.src.agent.graphs.query.session import QuerySessionManager
from apps.core.src.agent.graphs.query.worker import QueryWorker
from apps.core.src.agent.graphs.support.worker import SupportWorker
from apps.core.src.agent.graphs.transfer.services.extractor import TransferEntityExtractor
from apps.core.src.agent.graphs.transfer.worker import TransferWorker
from apps.core.src.agent.orchestrator import OrchestratorAgent
from apps.core.src.agent.orchestrator.config import OrchestratorDependencies
from apps.core.src.agent.orchestrator.services import MediaService
from apps.core.src.queue_consumers import MessageConsumer, TransactionConsumer
from apps.core.src.queue_consumers.flow_event_consumer import FlowEventConsumer
from apps.core.src.queue_consumers.outbox_consumer import OutboxConsumer
from shared.cache.bank_cache import BankCacheService
from shared.cache.flow_session_manager import FlowSessionManager
from shared.cache.redis_client import RedisClient
from shared.cache.user_data import UserDataCache
from shared.clients.factories.payment import PaymentProviderFactory
from shared.clients.providers.mono.banking import MonoBankingProvider
from shared.clients.providers.mono.direct_debit import MonoDirectDebitProvider
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


def setup_dependencies() -> tuple[MessageConsumer, TransactionConsumer, FlowEventConsumer, OutboxConsumer]:
    """Setup deps"""
    whatsapp_client = WhatsAppClient()
    redis_queue = RedisQueue(redis_url=settings.redis_url)
    user_repository = UserRepository(db=get_db_session())

    shared_redis = RedisClient.get_client()

    user_data_cache = UserDataCache(redis_client=shared_redis)

    onboarding_service = OnboardingService(redis_queue)
    onboarding_executor = OnboardingExecutor(user_repository, onboarding_service)

    beneficiary_repository = BeneficiaryRepository(db=get_db_session())
    account_repository = AccountRepository(db=get_db_session())
    actionable_message_repository = ActionableMessageRepository(db=get_db_session())

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    banking_provider = MonoBankingProvider()

    account_worker = AccountWorker(
        account_repo=account_repository,
        user_repo=user_repository,
        llm=llm,
        banking_provider=banking_provider,
        session_manager=FlowSessionManager(),
        direct_debit_provider=MonoDirectDebitProvider(),
    )

    transaction_repository = TransactionRepository(db=get_db_session())

    bill_provider = PaymentProviderFactory.get_bill_payment_provider()
    data_worker = None
    if bill_provider:
        data_worker = DataWorker(
            extractor=DataEntityExtractor(llm),
            bill_provider=bill_provider,
            transaction_repo=transaction_repository,
            queue=redis_queue,
        )

    support_worker = SupportWorker(
        llm=llm,
        transaction_repo=transaction_repository,
        actionable_message_repo=actionable_message_repository,
        redis_client=shared_redis,
        db_session=get_db_session(),
    )

    faq_worker = FAQWorker(
        llm=llm,
        get_db=get_db_session,
    )

    query_session_manager = QuerySessionManager(redis_client=shared_redis)
    query_worker = QueryWorker(
        llm=llm,
        banking_provider=banking_provider,
        session_manager=query_session_manager,
    )

    task_queue_service = TaskQueueService()
    conversation_responder = ConversationResponder(llm)

    bank_cache_service = BankCacheService(redis_client=shared_redis)

    agent_transfer_worker = TransferWorker(
        beneficiary_repo=beneficiary_repository,
        account_repo=account_repository,
        queue=redis_queue,
        extractor=TransferEntityExtractor(llm),
        banking_provider=banking_provider,
        bank_cache=bank_cache_service,
        transaction_repo=transaction_repository,
    )

    agent_airtime_worker = AirtimeWorker(
        extractor=AirtimeEntityExtractor(llm),
        bill_provider=bill_provider,
        transaction_repo=transaction_repository,
        queue=redis_queue,
    )

    media_service = MediaService(whatsapp_client)
    beneficiary_suggestion_service = BeneficiarySuggestionService(
        queue=redis_queue,
        redis_client=shared_redis,
    )

    orchestrator_deps = OrchestratorDependencies(
        llm=llm,
        user_repo=user_repository,
        beneficiary_repo=beneficiary_repository,
        actionable_message_repo=actionable_message_repository,
        whatsapp_client=whatsapp_client,
        task_queue_service=task_queue_service,
        conversation_responder=conversation_responder,
        transfer_service=agent_transfer_worker,
        airtime_service=agent_airtime_worker,
        query_service=query_worker,
        account_service=account_worker,
        media_service=media_service,
        data_service=data_worker,
        support_service=support_worker,
        faq_service=faq_worker,
        user_cache=user_data_cache,
        account_repo=account_repository,
        redis_client=shared_redis,
        banking_provider=banking_provider,
        queue=redis_queue,
        beneficiary_suggestion_service=beneficiary_suggestion_service,
    )

    orchestrator = OrchestratorAgent(orchestrator_deps)

    message_consumer = MessageConsumer(
        redis_queue=redis_queue,
        user_repository=user_repository,
        onboarding_executor=onboarding_executor,
        orchestrator=orchestrator,
    )

    transfer_executor = TransferExecutor(
        banking_provider=banking_provider,
        transaction_repo=transaction_repository,
    )

    airtime_executor = AirtimeExecutor(
        bill_provider=bill_provider,
        transaction_repo=transaction_repository,
        queue=redis_queue,
    )

    data_executor = None
    if bill_provider:
        data_executor = DataExecutor(
            bill_provider=bill_provider,
            transaction_repo=transaction_repository,
        )

    transaction_consumer = TransactionConsumer(
        redis_queue=redis_queue,
        transfer_executor=transfer_executor,
        airtime_executor=airtime_executor,
        data_executor=data_executor,
    )

    flow_event_consumer = FlowEventConsumer(
        redis_queue=redis_queue,
        orchestrator=orchestrator,
    )

    outbox_consumer = OutboxConsumer(
        redis_queue=redis_queue,
        messaging_client=whatsapp_client,
    )

    return message_consumer, transaction_consumer, flow_event_consumer, outbox_consumer
