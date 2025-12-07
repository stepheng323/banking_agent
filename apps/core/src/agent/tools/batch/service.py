"""Batch service for handling batch transaction execution."""

import asyncio
from typing import Optional, Any, Dict, TYPE_CHECKING

from shared.clients.whatsapp_client import WhatsAppClient
from apps.core.src.agent.orchestrator.services.task_queue_service import TaskQueueService
from apps.core.src.agent.tools.batch.executor import execute_batch

if TYPE_CHECKING:
    from apps.core.src.agent.sub_agents.transfer.service import TransferService
    from apps.core.src.agent.sub_agents.airtime.service import AirtimeService


class BatchService:
    """Service for managing batch operations."""

    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        task_queue_service: TaskQueueService,
        transfer_service: "TransferService",
        airtime_service: Optional["AirtimeService"] = None,
    ):
        self.whatsapp_client = whatsapp_client
        self.task_queue_service = task_queue_service
        self.transfer_service = transfer_service
        self.airtime_service = airtime_service

    async def resume_after_pin_verification(
        self,
        phone_number: str,
        pin_verified: bool,
        extra_data: Optional[Dict[str, Any]] = None,
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
            phone_number,
            "✅ PIN verified. Processing your transactions..."
        )

        asyncio.create_task(
            execute_batch(
                phone_number=phone_number,
                pin_verified=pin_verified,
                whatsapp_client=self.whatsapp_client,
                task_queue_service=self.task_queue_service,
                transfer_service=self.transfer_service,
                airtime_service=self.airtime_service,
            )
        )

        return "Processing transactions..."

