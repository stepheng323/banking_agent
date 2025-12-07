"""Dependency factories for flow webhook handlers."""

from typing import Optional, Any

from langchain_openai import ChatOpenAI

from shared.clients.whatsapp_client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue
from shared.cache.redis_client import RedisClient
from shared.repositories.account_repository import AccountRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.database.connection import get_db_session
from apps.core.src.agent.tools.cache.user_data import UserDataCache
from apps.core.src.agent.orchestrator.services.task_queue_service import TaskQueueService

from apps.gateway.core.config import settings

# Import agent services
try:
    from apps.core.src.agent.sub_agents.airtime.service import AirtimeService
    from apps.core.src.agent.sub_agents.transfer.service import TransferService
    from apps.core.src.agent.tools.batch.service import BatchService
    print("✅ Successfully imported AirtimeService, TransferService, and BatchService")
except ImportError as e:
    print(f"❌ Failed to import Agent Services: {e}")
    import traceback
    traceback.print_exc()
    AirtimeService = None  # type: ignore
    TransferService = None  # type: ignore
    BatchService = None # type: ignore
except Exception as e:
    print(f"❌ Unexpected error importing Agent Services: {e}")
    import traceback
    traceback.print_exc()
    AirtimeService = None  # type: ignore
    TransferService = None  # type: ignore
    BatchService = None # type: ignore


_redis_queue_instance = None
_airtime_service_instance = None
_transfer_service_instance = None
_batch_service_instance = None
_task_queue_service_instance = None


def get_redis_queue() -> RedisQueue:
    """Dependency factory for Redis queue with lazy initialization."""
    global _redis_queue_instance
    if _redis_queue_instance is None:
        _redis_queue_instance = RedisQueue(redis_url=settings.redis_url)
    return _redis_queue_instance


def get_whatsapp_client() -> WhatsAppClient:
    """
    Dependency factory for WhatsApp client.
    FastAPI will cache this dependency per request automatically.
    """
    return WhatsAppClient()


def get_task_queue_service() -> TaskQueueService:
    """Dependency factory for TaskQueueService."""
    global _task_queue_service_instance
    if _task_queue_service_instance is None:
        _task_queue_service_instance = TaskQueueService(redis_client=RedisClient.get_client())
    return _task_queue_service_instance


def get_airtime_service() -> Optional[Any]:
    """Dependency factory for AirtimeService with lazy initialization."""
    global _airtime_service_instance
    
    if AirtimeService is None:
        print("⚠️  AirtimeService not available (import failed)")
        return None
    
    if _airtime_service_instance is None:
        try:
            # Initialize dependencies
            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
            redis_client = RedisClient.get_client()
            user_cache = UserDataCache(redis_client=redis_client)
            account_repo = AccountRepository(db=get_db_session())
            beneficiary_repo = BeneficiaryRepository(db=get_db_session())
            whatsapp_client = WhatsAppClient()
            redis_queue = get_redis_queue()
            
            # Create service instance
            _airtime_service_instance = AirtimeService(
                llm=llm,
                user_cache=user_cache,
                account_repo=account_repo,
                beneficiary_repo=beneficiary_repo,
                whatsapp_client=whatsapp_client,
                queue=redis_queue,
                completion_callback=None,
            )
            print("✅ AirtimeService instance created")
        except Exception as e:
            print(f"❌ Error creating AirtimeService: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    return _airtime_service_instance


def get_transfer_service() -> Optional[Any]:
    """Dependency factory for TransferService with lazy initialization."""
    global _transfer_service_instance
    
    if TransferService is None:
        print("⚠️  TransferService not available (import failed)")
        return None
    
    if _transfer_service_instance is None:
        try:
            # Initialize dependencies
            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
            redis_client = RedisClient.get_client()
            user_cache = UserDataCache(redis_client=redis_client)
            account_repo = AccountRepository(db=get_db_session())
            beneficiary_repo = BeneficiaryRepository(db=get_db_session())
            whatsapp_client = WhatsAppClient()
            redis_queue = get_redis_queue()
            
            # Create service instance
            _transfer_service_instance = TransferService(
                llm=llm,
                user_cache=user_cache,
                beneficiary_repo=beneficiary_repo,
                account_repo=account_repo,
                whatsapp_client=whatsapp_client,
                queue=redis_queue,
                completion_callback=None,
            )
            print("✅ TransferService instance created")
        except Exception as e:
            print(f"❌ Error creating TransferService: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    return _transfer_service_instance


def get_batch_service() -> Optional[Any]:
    """Dependency factory for BatchService."""
    global _batch_service_instance
    
    if BatchService is None:
        print("⚠️  BatchService not available (import failed)")
        return None
        
    if _batch_service_instance is None:
        try:
            whatsapp_client = WhatsAppClient()
            task_queue_service = get_task_queue_service()
            transfer_service = get_transfer_service()
            airtime_service = get_airtime_service()
            
            _batch_service_instance = BatchService(
                whatsapp_client=whatsapp_client,
                task_queue_service=task_queue_service,
                transfer_service=transfer_service,
                airtime_service=airtime_service
            )
            print("✅ BatchService instance created")
        except Exception as e:
            print(f"❌ Error creating BatchService: {e}")
            import traceback
            traceback.print_exc()
            return None
            
    return _batch_service_instance
