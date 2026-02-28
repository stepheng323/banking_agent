from langchain_openai import ChatOpenAI

from apps.core.src.agent.executors.airtime import AirtimeExecutor
from apps.core.src.agent.executors.data import DataExecutor
from apps.core.src.agent.executors.payout import PayoutExecutor
from apps.core.src.agent.executors.transfer import TransferExecutor
from apps.core.src.agent.graphs.__shared__.beneficiary.suggestion_service import BeneficiarySuggestionService
from apps.core.src.agent.graphs.account import AccountWorker
from apps.core.src.agent.graphs.airtime import AirtimeWorker
from apps.core.src.agent.graphs.airtime.extractor import AirtimeEntityExtractor
from apps.core.src.agent.graphs.data import DataWorker as AgentDataWorker
from apps.core.src.agent.graphs.data.extractor import DataEntityExtractor
from apps.core.src.agent.graphs.faq import FAQWorker
from apps.core.src.agent.graphs.onboarding.executor import OnboardingExecutor
from apps.core.src.agent.graphs.onboarding.service import OnboardingService
from apps.core.src.agent.graphs.query.session import QuerySessionManager
from apps.core.src.agent.graphs.query.worker import QueryWorker as AgentQueryWorker
from apps.core.src.agent.graphs.support import SupportWorker
from apps.core.src.agent.graphs.transfer import TransferWorker
from apps.core.src.agent.graphs.transfer.services.extractor import TransferEntityExtractor
from apps.core.src.agent.orchestrator import OrchestratorAgent
from apps.core.src.agent.orchestrator.config import OrchestratorDependencies
from apps.core.src.agent.orchestrator.services.media_service import MediaService
from apps.core.src.queue_consumers import (
    FundingConsumer,
    MessageConsumer,
    OutboxConsumer,
    PayoutConsumer,
    RefundConsumer,
    TransactionConsumer,
)
from apps.core.src.queue_consumers.actionable_consumer import ActionableMessageConsumer
from apps.core.src.queue_consumers.flow_event_consumer import FlowEventConsumer
from shared.cache.bank_cache import BankCacheService
from shared.cache.redis_client import RedisClient
from shared.cache.user_data import UserDataCache
from shared.clients.abstractions.messaging import MessagingClient
from shared.clients.factories.payment import PaymentProviderFactory
from shared.clients.providers.mono.banking import MonoBankingProvider
from shared.clients.providers.mono.direct_debit import MonoDirectDebitProvider
from shared.clients.telegram.client import TelegramClient
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config import settings
from shared.database.connection import get_db_session
from shared.i18n import validate_catalog_completeness
from shared.policy import get_cached_policy, validate_policy_coverage
from shared.queue.factory import QueuePublisherFactory
from shared.queue.sqs_consumer import SQSQueueConsumer
from shared.repositories import AccountRepository, BeneficiaryRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.transaction_repository import TransactionRepository
from shared.repositories.user_repository import UserRepository
from shared.services import ConversationResponder
from shared.services.onboarding import session_manager as onboarding_session_manager
from shared.services.task_planner import refresh_planner_system_prompt
from shared.services.task_queue import TaskQueueService as TaskQueue


def _require_aws_account_id() -> None:
    if not settings.aws_account_id:
        raise RuntimeError("AWS_ACCOUNT_ID is required for AWS-only queue runtime")


def _build_messaging_clients() -> dict[str, MessagingClient]:
    whatsapp_client = WhatsAppClient()
    telegram_client = TelegramClient()
    return {
        "whatsapp": whatsapp_client,
        "telegram": telegram_client,
    }


def setup_core_consumers() -> tuple[MessageConsumer, FlowEventConsumer]:
    """Setup chat-critical consumers owned by ECS core."""
    validate_catalog_completeness()
    policy = get_cached_policy(force_reload=True)
    validate_policy_coverage(policy)
    refresh_planner_system_prompt()

    _require_aws_account_id()
    sqs_consumer = SQSQueueConsumer(
        region_name=settings.aws_region,
        account_id=settings.aws_account_id,
        project_name=settings.project_name,
        environment=settings.environment,
    )

    queue_publisher = QueuePublisherFactory.get_publisher()
    messaging_clients = _build_messaging_clients()

    user_repository = UserRepository(db=get_db_session())
    shared_redis = RedisClient.get_client()
    user_data_cache = UserDataCache(redis_client=shared_redis)

    onboarding_service = OnboardingService(queue_publisher)
    onboarding_executor = OnboardingExecutor(user_repository, onboarding_service)

    beneficiary_repository = BeneficiaryRepository(db=get_db_session())
    account_repository = AccountRepository(db=get_db_session())
    actionable_message_repository = ActionableMessageRepository(db=get_db_session())
    transaction_repository = TransactionRepository(db=get_db_session())

    beneficiary_suggestion_service = BeneficiarySuggestionService(queue_publisher)
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    banking_provider = MonoBankingProvider()
    direct_debit_provider = MonoDirectDebitProvider()

    account_worker = AccountWorker(
        account_repo=account_repository,
        user_repo=user_repository,
        llm=llm,
        banking_provider=banking_provider,
        session_manager=onboarding_session_manager,
        direct_debit_provider=direct_debit_provider,
    )

    bill_provider = PaymentProviderFactory.get_bill_payment_provider()
    data_worker = AgentDataWorker(
        extractor=DataEntityExtractor(llm=llm),
        bill_provider=bill_provider,
        transaction_repo=transaction_repository,
        publisher=queue_publisher,
    )

    support_worker = SupportWorker(
        llm=llm,
        transaction_repo=transaction_repository,
        actionable_message_repo=actionable_message_repository,
        redis_client=shared_redis,
        db_session=get_db_session(),
    )

    query_session_manager = QuerySessionManager(shared_redis)
    query_worker = AgentQueryWorker(
        llm=llm,
        banking_provider=banking_provider,
        session_manager=query_session_manager,
    )

    task_queue_service = TaskQueue()
    conversation_responder = ConversationResponder(llm)

    bank_cache_service = BankCacheService(redis_client=shared_redis)

    agent_airtime_worker = AirtimeWorker(
        extractor=AirtimeEntityExtractor(llm=llm),
        bill_provider=bill_provider,
        transaction_repo=transaction_repository,
        publisher=queue_publisher,
    )

    faq_worker = FAQWorker(
        llm=llm,
        get_db=get_db_session,
    )

    agent_transfer_worker = TransferWorker(
        validation_service=None,
        publisher=queue_publisher,
        extractor=TransferEntityExtractor(llm=llm),
        banking_provider=banking_provider,
        bank_cache=bank_cache_service,
        transaction_repo=transaction_repository,
        dd_provider=direct_debit_provider,
        redis_client=shared_redis,
    )

    media_service = MediaService(messaging_clients)

    orchestrator_deps = OrchestratorDependencies(
        llm=llm,
        user_repo=user_repository,
        beneficiary_repo=beneficiary_repository,
        actionable_message_repo=actionable_message_repository,
        task_queue_service=task_queue_service,
        conversation_responder=conversation_responder,
        transfer_service=agent_transfer_worker,
        airtime_service=agent_airtime_worker,
        query_service=query_worker,
        support_service=support_worker,
        account_service=account_worker,
        media_service=media_service,
        data_service=data_worker,
        user_cache=user_data_cache,
        faq_service=faq_worker,
        publisher=queue_publisher,
        beneficiary_suggestion_service=beneficiary_suggestion_service,
        account_repo=account_repository,
        redis_client=shared_redis,
        banking_provider=banking_provider,
    )

    orchestrator = OrchestratorAgent(orchestrator_deps)

    message_consumer = MessageConsumer(
        publisher=queue_publisher,
        user_repository=user_repository,
        onboarding_executor=onboarding_executor,
        orchestrator=orchestrator,
        queue_consumer=sqs_consumer,
    )

    flow_event_consumer = FlowEventConsumer(
        publisher=queue_publisher,
        orchestrator=orchestrator,
        queue_consumer=sqs_consumer,
    )

    return message_consumer, flow_event_consumer


def setup_transaction_worker_consumers() -> tuple[
    TransactionConsumer,
    FundingConsumer,
    PayoutConsumer,
    RefundConsumer,
]:
    """Setup async transaction-domain consumers owned by transaction Lambda worker."""
    _require_aws_account_id()
    queue_publisher = QueuePublisherFactory.get_publisher()
    transaction_repository = TransactionRepository(db=get_db_session())
    banking_provider = MonoBankingProvider()
    direct_debit_provider = MonoDirectDebitProvider()
    payout_provider = PaymentProviderFactory.get_provider_by_name("flutterwave")
    if payout_provider is None:
        payout_provider = PaymentProviderFactory.create_provider("flutterwave")
    if payout_provider is None:
        raise RuntimeError("Flutterwave payout provider is not configured")

    bill_provider = PaymentProviderFactory.get_bill_payment_provider()
    airtime_executor = AirtimeExecutor(
        bill_provider=bill_provider,
        transaction_repo=transaction_repository,
        publisher=queue_publisher,
    )
    transfer_executor = TransferExecutor(
        banking_provider=banking_provider,
        transaction_repo=transaction_repository,
    )
    data_executor = DataExecutor(
        bill_provider=bill_provider,
        transaction_repo=transaction_repository,
    )

    transaction_consumer = TransactionConsumer(
        transfer_executor=transfer_executor,
        airtime_executor=airtime_executor,
        data_executor=data_executor,
    )

    funding_consumer = FundingConsumer(
        publisher=queue_publisher,
        direct_debit_provider=direct_debit_provider,
    )
    payout_consumer = PayoutConsumer(
        payout_executor=PayoutExecutor(payment_provider=payout_provider),
    )
    refund_consumer = RefundConsumer(direct_debit_provider=direct_debit_provider)

    return (
        transaction_consumer,
        funding_consumer,
        payout_consumer,
        refund_consumer,
    )


def setup_messaging_worker_consumers() -> tuple[OutboxConsumer, ActionableMessageConsumer]:
    """Setup async messaging-domain consumers owned by messaging Lambda worker."""
    _require_aws_account_id()
    queue_publisher = QueuePublisherFactory.get_publisher()
    messaging_clients = _build_messaging_clients()
    outbox_consumer = OutboxConsumer(
        publisher=queue_publisher,
        messaging_clients=messaging_clients,
    )
    actionable_consumer = ActionableMessageConsumer()
    return outbox_consumer, actionable_consumer
