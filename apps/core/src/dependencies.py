from langchain_openai import ChatOpenAI

from apps.core.src.agent.orchestrator import OrchestratorAgent
from apps.core.src.agent.orchestrator.services import (
    ConversationResponder,
    MediaService,
    TaskExecutor,
    TaskQueueService,
)
from apps.core.src.agent.sub_agents.account_management.service import AccountManagementService
from apps.core.src.agent.sub_agents.airtime import AirtimeService
from apps.core.src.agent.sub_agents.airtime.completion import AirtimeCompletionService
from apps.core.src.agent.sub_agents.airtime.executor import AirtimeExecutor
from apps.core.src.agent.sub_agents.data import DataPurchaseGraph
from apps.core.src.agent.sub_agents.onboarding.executor import OnboardingExecutor
from apps.core.src.agent.sub_agents.onboarding.service import OnboardingService
from apps.core.src.agent.sub_agents.query.graph import QueryFlowGraph
from apps.core.src.agent.sub_agents.support.graph import SupportFlowGraph
from apps.core.src.agent.sub_agents.transfer import TransferService as AgentTransferService
from apps.core.src.agent.sub_agents.transfer.completion import TransferCompletionService
from apps.core.src.agent.sub_agents.transfer.executor import TransferExecutor
from apps.core.src.agent.tools.beneficiary.suggestion_service import BeneficiarySuggestionService
from apps.core.src.queue_consumers import MessageConsumer, TransactionConsumer
from apps.core.src.queue_consumers.flow_event_consumer import FlowEventConsumer
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


def setup_dependencies():
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

    transfer_completion_service = TransferCompletionService(
        whatsapp_client=whatsapp_client,
        redis_client=shared_redis,
        beneficiary_repository=beneficiary_repository,
        actionable_message_repo=actionable_message_repository,
        beneficiary_suggestion_service=beneficiary_suggestion_service,
    )

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    banking_provider = MonoBankingProvider()
    direct_debit_provider = MonoDirectDebitProvider()

    query_graph = QueryFlowGraph(
        llm=llm,
        banking_provider=banking_provider,
        redis_client=shared_redis,
    )
    account_management_service = AccountManagementService(
        account_repo=account_repository,
        user_repo=user_repository,
        llm=llm,
        whatsapp_client=whatsapp_client,
        direct_debit_provider=direct_debit_provider,
    )

    bill_provider = PaymentProviderFactory.get_bill_payment_provider()
    data_graph = None
    if bill_provider:
        data_graph = DataPurchaseGraph(
            bill_provider=bill_provider,
            redis_client=shared_redis,
        )

    transaction_repository = TransactionRepository(db=get_db_session())
    support_graph = SupportFlowGraph(
        llm=llm,
        transaction_repo=transaction_repository,
        actionable_message_repo=actionable_message_repository,
        redis_client=shared_redis,
    )

    task_queue_service = TaskQueueService()
    conversation_responder = ConversationResponder(llm)

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
    )

    task_executor = TaskExecutor(
        transfer_service=agent_transfer_service,
        airtime_service=agent_airtime_service,
        task_queue_service=task_queue_service,
        query_graph=query_graph,
        completion_callback=None,
    )

    media_service = MediaService(whatsapp_client)

    orchestrator = OrchestratorAgent(
        llm=llm,
        user_repo=user_repository,
        beneficiary_repo=beneficiary_repository,
        actionable_message_repo=actionable_message_repository,
        whatsapp_client=whatsapp_client,
        task_queue_service=task_queue_service,
        conversation_responder=conversation_responder,
        transfer_service=agent_transfer_service,
        airtime_service=agent_airtime_service,
        task_executor=task_executor,
        query_graph=query_graph,
        account_management_service=account_management_service,
        media_service=media_service,
        data_graph=data_graph,
        support_graph=support_graph,
    )

    completion_callback = orchestrator.completion_callback
    agent_transfer_service.graph.completion_callback = completion_callback
    agent_airtime_service.graph.completion_callback = completion_callback
    task_executor.completion_callback = completion_callback

    message_consumer = MessageConsumer(
        redis_queue=redis_queue,
        user_repository=user_repository,
        onboarding_executor=onboarding_executor,
        orchestrator=orchestrator,
        whatsapp_client=whatsapp_client,
    )

    airtime_completion_service = AirtimeCompletionService(
        whatsapp_client=whatsapp_client,
        redis_client=shared_redis,
        actionable_message_repo=actionable_message_repository,
        beneficiary_suggestion_service=beneficiary_suggestion_service,
    )
    airtime_executor = AirtimeExecutor(airtime_service=airtime_completion_service)
    transfer_executor = TransferExecutor(transfer_service=transfer_completion_service)

    transaction_consumer = TransactionConsumer(
        redis_queue=redis_queue,
        transfer_executor=transfer_executor,
        airtime_executor=airtime_executor,
        data_handler=data_graph,
    )

    flow_event_consumer = FlowEventConsumer(
        redis_queue=redis_queue,
        transfer_service=agent_transfer_service,
        airtime_service=agent_airtime_service,
        batch_service=None,  # Batch service created on-demand via BatchService
        whatsapp_client=whatsapp_client,
    )

    return message_consumer, transaction_consumer, flow_event_consumer
