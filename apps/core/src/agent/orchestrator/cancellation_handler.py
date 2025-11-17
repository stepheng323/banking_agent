"""Cancellation handler for the orchestrator."""

from typing import Optional
import asyncio

from shared.cache.redis_client import RedisClient
from apps.core.src.agent.models.classification import ClassificationResult
from apps.core.src.agent.transfer import TransferService
from apps.core.src.agent.airtime import AirtimeService
from apps.core.src.agent.orchestrator.context_manager import OrchestratorContextManager


class OrchestratorCancellationHandler:
    """Handles cancellation requests."""

    def __init__(
        self,
        transfer_service: TransferService,
        airtime_service: AirtimeService,
        context_manager: OrchestratorContextManager,
    ) -> None:
        self.transfer_service = transfer_service
        self.airtime_service = airtime_service
        self.context_manager = context_manager

    async def handle_cancellation(
        self,
        phone_number: str,
        text: str,
        result: ClassificationResult,
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
        has_active_transaction = False
        active_flow = None
        flow_state = None
        transfer_status = None

        if conversation_state:
            active_flow = conversation_state.get("active_flow")
            flow_state = conversation_state.get("flow_state")
            transfer_status = conversation_state.get("transfer_status")

            if (active_flow and
                (flow_state not in ("extracting", "error", "cancelled", None) or
                 transfer_status == "pending")):
                has_active_transaction = True

        if not has_active_transaction:
            try:
                redis_client = RedisClient.get_client()
                pending_transfer = await redis_client.get(f"user:{phone_number}:pending_transfer")
                if pending_transfer:
                    has_active_transaction = True
                    active_flow = "transfer"
                    transfer_status = "pending"
                    print(
                        "✅ Found active transaction via pending_transfer fallback")
            except Exception as e:
                print(f"⚠️  Error checking pending_transfer: {e}")

        if has_active_transaction:
            if active_flow == "transfer":
                cancel_classification_dict = result.model_dump() if hasattr(result, 'model_dump') else {
                    "intent": result.intent,
                    "is_cancellation": result.is_cancellation,
                    "confidence": result.confidence,
                }
                cancel_response = await self.transfer_service.run_simple(phone_number, text, cancel_classification_dict)
                asyncio.create_task(
                    self.context_manager.save_last_response(phone_number, cancel_response))
                return cancel_response
            elif active_flow in ("airtime", "data"):
                cancel_response = "Cancellation for airtime/data flows will be implemented soon."
                asyncio.create_task(
                    self.context_manager.save_last_response(phone_number, cancel_response))
                return cancel_response

        cancel_response = "There's no active transaction to cancel."
        asyncio.create_task(
            self.context_manager.save_last_response(phone_number, cancel_response))
        return cancel_response

