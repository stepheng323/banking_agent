from langchain_openai import ChatOpenAI

from shared.clients.s3_client import S3Client
from shared.clients.mono_client import MonoClient
from shared.config import settings
from shared.clients.whatsapp_client import WhatsAppClient
from shared.database.connection import get_db_session
from shared.queue.redis_queue import RedisQueue
from shared.repositories import BeneficiaryRepository, AccountRepository
from shared.repositories.user_repository import UserRepository
from shared.cache.redis_client import RedisClient
from shared.services.receipt_generator import ReceiptGenerator
from apps.core.src.agent.tools.cache.user_data import UserDataCache

from apps.core.src.agent.orchestrator import OrchestratorAgent
from apps.core.src.agent.orchestrator.services import ConversationResponder, TaskQueueService, TaskExecutor, MediaService
from apps.core.src.agent.sub_agents.transfer import TransferService as AgentTransferService
from apps.core.src.agent.sub_agents.airtime import AirtimeService
from apps.core.src.queue_consumers import MessageConsumer, TransactionConsumer
from apps.core.src.agent.sub_agents.onboarding.executor import OnboardingExecutor
from apps.core.src.agent.sub_agents.onboarding.service import OnboardingService
from apps.core.src.agent.sub_agents.transfer.executor import TransferExecutor
from apps.core.src.agent.sub_agents.airtime.executor import AirtimeExecutor
from apps.core.src.agent.sub_agents.transfer.completion import TransferCompletionService
from apps.core.src.agent.sub_agents.airtime.completion import AirtimeCompletionService
from apps.core.src.agent.tools.beneficiary.suggestion_service import BeneficiarySuggestionService
from apps.core.src.agent.sub_agents.query.service import QueryService
from apps.core.src.agent.sub_agents.account_management.service import AccountManagementService


def setup_dependencies():
    """Setup deps"""
    whatsapp_client = WhatsAppClient()
    redis_queue = RedisQueue(redis_url=settings.redis_url)
    user_repository = UserRepository(db=get_db_session())

    shared_redis = RedisClient.get_client()

    # Create UserDataCache for agent services
    user_data_cache = UserDataCache(redis_client=shared_redis)

    onboarding_service = OnboardingService(whatsapp_client)
    onboarding_executor = OnboardingExecutor(
        whatsapp_client, user_repository, onboarding_service)

    beneficiary_repository = BeneficiaryRepository(db=get_db_session())
    account_repository = AccountRepository(db=get_db_session())
    receipt_generator = ReceiptGenerator()
    s3_client = S3Client()

    beneficiary_suggestion_service = BeneficiarySuggestionService(
        whatsapp_client=whatsapp_client,
        redis_client=shared_redis,
    )

    transfer_completion_service = TransferCompletionService(
        whatsapp_client=whatsapp_client,
        redis_client=shared_redis,
        beneficiary_repository=beneficiary_repository,
        receipt_generator=receipt_generator,
        s3_client=s3_client,
        beneficiary_suggestion_service=beneficiary_suggestion_service,
    )


    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    # Mono API client for transaction queries
    mono_client = MonoClient(api_key=settings.mono_api_key)
    query_service = QueryService(
        llm=llm,
        mono_client=mono_client,
        user_repo=user_repository,
        account_repo=account_repository
    )
    account_management_service = AccountManagementService(
        account_repo=account_repository,
        user_repo=user_repository,
        llm=llm,
        whatsapp_client=whatsapp_client
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
        completion_callback=None,
    )

    agent_airtime_service = AirtimeService(
        llm=llm,
        user_cache=user_data_cache,
        account_repo=account_repository,
        beneficiary_repo=beneficiary_repository,
        whatsapp_client=whatsapp_client,
        queue=redis_queue,
        completion_callback=None,
    )

    task_executor = TaskExecutor(
        transfer_service=agent_transfer_service,
        airtime_service=agent_airtime_service,
        task_queue_service=task_queue_service,
        completion_callback=None,
    )


    media_service = MediaService(whatsapp_client)

    orchestrator = OrchestratorAgent(
        llm=llm,
        user_repo=user_repository,
        beneficiary_repo=beneficiary_repository,
        whatsapp_client=whatsapp_client,
        task_queue_service=task_queue_service,
        conversation_responder=conversation_responder,
        transfer_service=agent_transfer_service,
        airtime_service=agent_airtime_service,
        task_executor=task_executor,
        query_service=query_service,
        account_management_service=account_management_service,
        media_service=media_service,
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
        beneficiary_suggestion_service=beneficiary_suggestion_service,
    )
    airtime_executor = AirtimeExecutor(airtime_service=airtime_completion_service)
    transfer_executor = TransferExecutor(transfer_service=transfer_completion_service)

    transaction_consumer = TransactionConsumer(
        redis_queue=redis_queue,
        transfer_executor=transfer_executor,
        airtime_executor=airtime_executor,
    )

    return message_consumer, transaction_consumer
