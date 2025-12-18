"""Cancellation handler for the orchestrator."""

from typing import Optional, TYPE_CHECKING
import asyncio

from shared.cache.redis_client import RedisClient
from apps.core.src.agent.orchestrator.models.classification import ClassificationResult
from apps.core.src.agent.sub_agents.transfer import TransferService
from apps.core.src.agent.sub_agents.airtime import AirtimeService
from apps.core.src.agent.orchestrator.features.context.service import OrchestratorContextManager
from shared.utils.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from shared.services.task_queue import TaskQueueService


class OrchestratorCancellationHandler:
    """Handles cancellation requests."""

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
        result: Optional[ClassificationResult],
        conversation_state: Optional[dict],
    ) -> Optional[str]:
        """
        Handle cancellation request.

        Args:
            phone_number: User's phone number
            text: User's message
            result: Classification result
            conversation_state: Current conversation state

        Returns:
            Response string if cancellation handled, None otherwise
        """
        # FIRST: Check for active task queue (complex intents with multiple tasks)
        if self.task_queue_service:
            has_active_queue = await self.task_queue_service.has_active_queue(phone_number)
            if has_active_queue:
                logger.info(f"Cancellation detected during active task queue for {phone_number}")
                
                # Get current task to determine if we need to clear transfer checkpoint
                current_task_id = await self.task_queue_service.get_current_task(phone_number)
                planner_output = await self.task_queue_service.get_task_queue(phone_number)
                current_task_executor = None
                
                if planner_output and current_task_id:
                    for task in planner_output.tasks:
                        if task.id == current_task_id:
                            current_task_executor = task.executor
                            break
                
                # Clear task queue
                await self.task_queue_service.clear_task_queue(phone_number)
                logger.info(f"Cleared task queue for {phone_number}")
                
                # Clear conversation state
                await self.context_manager.clear_conversation_state(phone_number)
                logger.info(f"Cleared conversation state for {phone_number}")
                
                # Clear transfer checkpoint if current task is a transfer
                if current_task_executor == "transfer":
                    await self.transfer_service.clear_checkpoint(phone_number)
                    logger.info(f"Cleared transfer checkpoint for {phone_number}")
                
                cancel_response = "All pending transfers have been cancelled."
                asyncio.create_task(
                    self.context_manager.save_last_response(phone_number, cancel_response))
                return cancel_response
        
        # SECOND: Check for active single transactions
        has_active_transaction = False
        active_flow = None
        flow_state = None
        transfer_status = None
        airtime_status = None

        if conversation_state:
            active_flow = conversation_state.get("active_flow")
            flow_state = conversation_state.get("flow_state")
            transfer_status = conversation_state.get("transfer_status")
            airtime_status = conversation_state.get("airtime_status")

            if (active_flow and
                (flow_state not in ("extracting", "error", "cancelled", None) or
                 transfer_status == "pending" or
                 airtime_status == "pending")):
                has_active_transaction = True

        if not has_active_transaction:
            try:
                redis_client = RedisClient.get_client()
                # Check for pending transfer
                pending_transfer = await redis_client.get(f"user:{phone_number}:pending_transfer")
                if pending_transfer:
                    has_active_transaction = True
                    active_flow = "transfer"
                    transfer_status = "pending"
                    logger.info(
                        "Found active transaction via pending_transfer fallback")
                
                # Check for pending airtime
                if not has_active_transaction:
                    pending_airtime = await redis_client.get(f"user:{phone_number}:pending_airtime")
                    if pending_airtime:
                        has_active_transaction = True
                        active_flow = "airtime"
                        airtime_status = "pending"
                        logger.info(
                            "Found active transaction via pending_airtime fallback")
            except Exception as e:
                logger.error(f"Error checking pending transactions: {e}")

        if has_active_transaction:
            # Create default cancel classification if result is None
            if result:
                cancel_classification_dict = result.model_dump() if hasattr(result, 'model_dump') else {
                    "intent": result.intent,
                    "is_cancellation": result.is_cancellation,
                    "confidence": result.confidence,
                }
            else:
                cancel_classification_dict = {
                    "intent": "cancel",
                    "is_cancellation": True,
                    "confidence": 1.0
                }

            if active_flow == "transfer":
                cancel_response = await self.transfer_service.run_simple(phone_number, text, cancel_classification_dict)
                asyncio.create_task(
                    self.context_manager.save_last_response(phone_number, cancel_response))
                return cancel_response
            elif active_flow == "airtime":
                cancel_response = await self.airtime_service.run_simple(phone_number, text, cancel_classification_dict)
                asyncio.create_task(
                    self.context_manager.save_last_response(phone_number, cancel_response))
                return cancel_response
            elif active_flow == "data":
                cancel_response = "Cancellation for data flows will be implemented soon."
                asyncio.create_task(
                    self.context_manager.save_last_response(phone_number, cancel_response))
                return cancel_response

        cancel_response = "There's no active transaction to cancel."
        asyncio.create_task(
            self.context_manager.save_last_response(phone_number, cancel_response))
        return cancel_response

