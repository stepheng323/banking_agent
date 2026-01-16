"""Flow control handler - handles fresh starts and cancellations."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.pipeline_stages.context_loader.service import OrchestratorContextManager
from apps.core.src.agent.shared.batch.utils import ExecutionState
from shared.cache.redis_client import RedisClient
from shared.utils.logging import get_logger

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.airtime import AirtimeService
    from apps.core.src.agent.graphs.transfer import TransferService
    from apps.core.src.agent.orchestrator.pipeline_stages.flow_control.service import (
        OrchestratorCancellationHandler,
    )

logger = get_logger(__name__)


FRESH_START_INDICATORS = {
    "hi", "hello", "hey", "good morning", "good afternoon",
    "good evening", "thanks", "thank you", "bye", "goodbye",
}


class FlowControlHandler(MessageHandler):
    """Handles fresh starts (clear stale state) and cancellations."""

    def __init__(
        self,
        context_manager: OrchestratorContextManager,
        cancellation_handler: "OrchestratorCancellationHandler",
        transfer_service: "TransferService",
        airtime_service: "AirtimeService",
    ):
        self.context_manager = context_manager
        self.cancellation_handler = cancellation_handler
        self.transfer_service = transfer_service
        self.airtime_service = airtime_service
        self.redis_client = RedisClient.get_client()

    async def can_handle(self, context: MessageContext) -> bool:
        """Can handle if cancellation OR fresh start with stale state."""
        if context.is_cancellation:
            return True

        if context.has_active_queue or not context.conversation_state:
            return False

        text_lower = context.text.strip().lower()
        return any(
            text_lower.startswith(ind) or text_lower == ind
            for ind in FRESH_START_INDICATORS
        )

    async def handle(self, context: MessageContext) -> MessageContext:
        """Handle cancellation or fresh start."""
        if context.is_cancellation:
            return await self._handle_cancellation(context)
        return await self._handle_fresh_start(context)

    async def _handle_fresh_start(self, context: MessageContext) -> MessageContext:
        """Clear stale conversation state and flow checkpoints."""
        await self.context_manager.clear_conversation_state(context.phone_number)
        await self.transfer_service.clear_checkpoint(context.phone_number)
        await self.airtime_service.clear_checkpoint(context.phone_number)
        logger.info("cleared_stale_state")
        return context.update(conversation_state=None)

    async def _handle_cancellation(self, context: MessageContext) -> MessageContext:
        """Handle cancellation request."""
        execution_state = await self.redis_client.get(
            f"queue:{context.phone_number}:execution_state"
        )

        if execution_state == ExecutionState.EXECUTING_BATCH:
            await self.redis_client.set(
                f"queue:{context.phone_number}:cancel_batch", "1", ex=300
            )
            response = (
                "⚠️ Cancelling batch execution...\n\n"
                "✓ Completed tasks will remain completed.\n"
                "⏳ Currently executing tasks will finish.\n"
                "❌ Pending tasks will be cancelled."
            )
            return context.with_response(response, handled=True)

        classifier_response = None
        if context.classification_result:
            classifier_response = context.classification_result.response

        response = await self.cancellation_handler.handle_cancellation(
            context.phone_number,
            context.text,
            context.classification_result,
            context.conversation_state,
            classifier_response=classifier_response,
        )

        if response:
            return context.with_response(response, handled=True)
        return context
