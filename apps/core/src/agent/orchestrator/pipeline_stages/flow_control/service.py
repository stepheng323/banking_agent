"""Cancellation handler for the orchestrator."""

import asyncio
from typing import TYPE_CHECKING, Optional

from apps.core.src.agent.orchestrator.pipeline_stages.context_loader.service import OrchestratorContextManager
from apps.core.src.agent.orchestrator.models.classification import ClassificationResult
from apps.core.src.agent.graphs.airtime import AirtimeService
from apps.core.src.agent.graphs.transfer import TransferService
from shared.utils.cancellation import cleanup_transaction_redis_keys
from shared.utils.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from shared.services.task_queue import TaskQueueService


class OrchestratorCancellationHandler:
    """Handles cancellation requests at the orchestrator level."""

    def __init__(
        self,
        transfer_service: TransferService,
        airtime_service: AirtimeService,
        context_manager: OrchestratorContextManager,
        task_queue_service: Optional["TaskQueueService"] = None,
    ) -> None:
        self.transfer_service = transfer_service
        self.airtime_service = airtime_service
        self.context_manager = context_manager
        self.task_queue_service = task_queue_service

    async def handle_cancellation(
        self,
        phone_number: str,
        text: str,
        result: ClassificationResult | None,
        conversation_state: dict | None,
        classifier_response: str | None = None,
    ) -> str | None:
        """Handle cancellation request."""
        if self.task_queue_service:
            queue_response = await self._handle_task_queue_cancellation(phone_number)
            if queue_response:
                return queue_response

        active_flow = self._detect_active_flow(conversation_state)
        
        if not active_flow:
            active_flow = await self._check_pending_transactions(phone_number)

        if active_flow:
            return await self._cancel_single_transaction(phone_number, active_flow, classifier_response)

        return await self._no_active_transaction_response(phone_number, classifier_response)

    async def _handle_task_queue_cancellation(self, phone_number: str) -> str | None:
        """Handle cancellation of multi-task queue."""
        has_active_queue = await self.task_queue_service.has_active_queue(phone_number)
        if not has_active_queue:
            return None

        logger.info(f"Cancellation during active task queue for {phone_number}")

        current_task_id = await self.task_queue_service.get_current_task(phone_number)
        planner_output = await self.task_queue_service.get_task_queue(phone_number)
        current_executor = None

        if planner_output and current_task_id:
            for task in planner_output.tasks:
                if task.id == current_task_id:
                    current_executor = task.executor
                    break

        await self.task_queue_service.clear_task_queue(phone_number)
        await self.context_manager.clear_conversation_state(phone_number)

        if current_executor == "transfer":
            await self.transfer_service.clear_checkpoint(phone_number)

        response = "All pending transfers have been cancelled."
        asyncio.create_task(self.context_manager.save_last_response(phone_number, response))
        return response

    def _detect_active_flow(self, conversation_state: dict | None) -> str | None:
        """Detect active flow from conversation state."""
        if not conversation_state:
            return None

        active_flow = conversation_state.get("active_flow")
        flow_state = conversation_state.get("flow_state")
        transfer_status = conversation_state.get("transfer_status")
        airtime_status = conversation_state.get("airtime_status")

        if active_flow and (
            flow_state not in ("extracting", "error", "cancelled", None)
            or transfer_status == "pending"
            or airtime_status == "pending"
        ):
            return active_flow

        return None

    async def _check_pending_transactions(self, phone_number: str) -> str | None:
        """Check Redis for pending transactions (fallback)."""
        try:
            from shared.cache.redis_client import RedisClient
            redis_client = RedisClient.get_client()

            pending_transfer = await redis_client.get(f"user:{phone_number}:pending_transfer")
            if pending_transfer:
                logger.info("Found pending transfer via fallback")
                return "transfer"

            pending_airtime = await redis_client.get(f"user:{phone_number}:pending_airtime")
            if pending_airtime:
                logger.info("Found pending airtime via fallback")
                return "airtime"

        except Exception as e:
            logger.error(f"Error checking pending transactions: {e}")

        return None

    async def _cancel_single_transaction(
        self, 
        phone_number: str, 
        active_flow: str,
        classifier_response: str | None
    ) -> str:
        """Cancel a single active transaction."""
        await self.context_manager.clear_conversation_state(phone_number)

        await cleanup_transaction_redis_keys(
            phone_number=phone_number,
            transaction_type=active_flow if active_flow in ("transfer", "airtime", "data") else "transfer",
        )

        if active_flow == "transfer":
            await self.transfer_service.clear_checkpoint(phone_number)
        elif active_flow == "airtime":
            await self.airtime_service.clear_checkpoint(phone_number)

        response = classifier_response or "Transaction cancelled. Anything else I can help with?"
        asyncio.create_task(self.context_manager.save_last_response(phone_number, response))
        logger.info("cancellation_completed", phone=phone_number, flow=active_flow)
        return response

    async def _no_active_transaction_response(
        self, 
        phone_number: str, 
        classifier_response: str | None
    ) -> str:
        """Handle case where nothing to cancel."""
        response = classifier_response or "There's nothing to cancel right now. How can I help?"
        asyncio.create_task(self.context_manager.save_last_response(phone_number, response))
        return response
