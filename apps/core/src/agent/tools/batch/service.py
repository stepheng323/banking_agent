"""Batch service for handling batch transaction execution."""

import asyncio
from typing import TYPE_CHECKING, Any, Optional

from apps.core.src.agent.tools.batch.executor import execute_batch
from shared.clients.whatsapp.client import WhatsAppClient
from shared.services.task_queue import TaskQueueService

if TYPE_CHECKING:
    from apps.core.src.agent.sub_agents.account_management.service import AccountManagementService
    from apps.core.src.agent.sub_agents.airtime.service import AirtimeService
    from apps.core.src.agent.sub_agents.query.graph import QueryFlowGraph
    from apps.core.src.agent.sub_agents.transfer.service import TransferService
    from shared.cache.user_data import UserDataCache


class BatchService:
    """Service for managing batch operations."""

    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        task_queue_service: TaskQueueService,
        transfer_service: "TransferService",
        airtime_service: Optional["AirtimeService"] = None,
        query_graph: Optional["QueryFlowGraph"] = None,
        user_cache: Optional["UserDataCache"] = None,
        account_management_service: Optional["AccountManagementService"] = None,
    ):
        self.whatsapp_client = whatsapp_client
        self.task_queue_service = task_queue_service
        self.transfer_service = transfer_service
        self.airtime_service = airtime_service
        self.query_graph = query_graph
        self.user_cache = user_cache
        self.account_management_service = account_management_service

    async def resume_after_pin_verification(
        self,
        phone_number: str,
        pin_verified: bool,
        extra_data: dict[str, Any] | None = None,
    ) -> str:
        """
        Resume batch execution after PIN verification.

        Args:
            phone_number: User's phone number
            pin_verified: Whether PIN was verified successfully
            extra_data: Optional extra data (unused for batch)

        Returns:
            Response message to display to user
        """
        await self.whatsapp_client.send_text(
            phone_number, "✓ PIN verified. Processing your transactions..."
        )

        asyncio.create_task(
            execute_batch(
                phone_number=phone_number,
                pin_verified=pin_verified,
                whatsapp_client=self.whatsapp_client,
                task_queue_service=self.task_queue_service,
                transfer_service=self.transfer_service,
                airtime_service=self.airtime_service,
                query_graph=self.query_graph,
                user_cache=self.user_cache,
                account_management_service=self.account_management_service,
            )
        )

        return "Processing transactions..."
