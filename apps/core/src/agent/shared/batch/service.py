"""Batch service for handling batch transaction execution."""

import asyncio
from typing import TYPE_CHECKING, Any, Optional

from apps.core.src.agent.shared.batch.executor import execute_batch_dag, retry_failed_tasks
from shared.cache.redis_client import RedisClient
from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue
from shared.services.task_queue import TaskQueueService
from shared.utils.logging import get_logger

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.account_management.service import AccountManagementService
    from apps.core.src.agent.graphs.airtime.service import AirtimeService
    from apps.core.src.agent.graphs.data.service import DataService
    from apps.core.src.agent.graphs.query import QueryService
    from apps.core.src.agent.graphs.transfer.service import TransferService
    from shared.cache.user_data import UserDataCache

logger = get_logger(__name__)


class BatchService:
    """Service for managing batch operations."""

    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        task_queue_service: TaskQueueService,
        transfer_service: "TransferService",
        queue: RedisQueue,
        airtime_service: Optional["AirtimeService"] = None,
        data_service: Optional["DataService"] = None,
        query_service: Optional["QueryService"] = None,
        user_cache: Optional["UserDataCache"] = None,
        account_management_service: Optional["AccountManagementService"] = None,
    ):
        self.whatsapp_client = whatsapp_client
        self.task_queue_service = task_queue_service
        self.transfer_service = transfer_service
        self.queue = queue
        self.airtime_service = airtime_service
        self.data_service = data_service
        self.query_service = query_service
        self.user_cache = user_cache
        self.account_management_service = account_management_service
        self.redis_client = RedisClient.get_client()

    async def resume_after_pin_verification(
        self,
        phone_number: str,
        pin_verified: bool,
        extra_data: dict[str, Any] | None = None,
    ) -> str:
        """
        Resume batch execution after PIN verification.

        Uses the DAG-based workflow executor for dependency-aware parallel execution.

        Args:
            phone_number: User's phone number
            pin_verified: Whether PIN was verified successfully
            extra_data: Optional extra data
        """
        await self.whatsapp_client.send_text(phone_number, "✓ PIN verified. Processing your transactions...")

        if pin_verified:
            from apps.core.src.agent.shared.batch.state_machine import BatchStateMachine
            from apps.core.src.agent.shared.batch.utils import ExecutionState

            sm = BatchStateMachine(self.redis_client, phone_number)
            if not await sm.transition_to(ExecutionState.AUTHORIZED):
                logger.warning(f"Invalid state transition for {phone_number} to AUTHORIZED")
                await self.whatsapp_client.send_text(
                    phone_number, "❌ Batch session invalid or expired. Please start over."
                )
                await self.task_queue_service.clear_task_queue(phone_number)
                return "Session invalid."

        user_id = ""
        if self.user_cache:
            profile = await self.user_cache.get_user_profile(phone_number)
            if profile:
                user_id = profile.get("id", "")

        if not user_id:
            logger.warning(f"Could not resolve user_id for {phone_number} in batch execution")

        asyncio.create_task(
            execute_batch_dag(
                phone_number=phone_number,
                pin_verified=pin_verified,
                user_id=user_id,
                whatsapp_client=self.whatsapp_client,
                task_queue_service=self.task_queue_service,
                redis_client=self.redis_client,
                queue=self.queue,
                query_service=self.query_service,
                user_cache=self.user_cache,
                account_management_service=self.account_management_service,
            )
        )

        return "Processing transactions..."

    async def retry_failed(self, phone_number: str) -> str:
        """
        Retry previously failed tasks.

        Only retries TRANSIENT failures (network, timeout, etc).
        """
        user_id = ""
        if self.user_cache:
            profile = await self.user_cache.get_user_profile(phone_number)
            if profile:
                user_id = profile.get("id", "")

        asyncio.create_task(
            retry_failed_tasks(
                phone_number=phone_number,
                user_id=user_id,
                whatsapp_client=self.whatsapp_client,
                task_queue_service=self.task_queue_service,
                redis_client=self.redis_client,
                queue=self.queue,
                query_service=self.query_service,
                user_cache=self.user_cache,
                account_management_service=self.account_management_service,
            )
        )

        return "Retrying failed tasks..."
