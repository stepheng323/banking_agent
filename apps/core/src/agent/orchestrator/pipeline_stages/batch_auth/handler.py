"""Batch authorization handler - handles batch authorization confirmation."""

import time
from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.services import TaskQueueService
from shared.cache.redis_client import RedisClient
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config import settings

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.transfer import TransferService
from shared.types.agent_types import TaskStatus
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class BatchAuthorizationHandler(MessageHandler):
    """
    Handles batch authorization trigger.

    Runs when all tasks are collection_complete to trigger the Auth Flow.
    """

    def __init__(
        self,
        task_queue_service: TaskQueueService,
        transfer_service: "TransferService",
        whatsapp_client: WhatsAppClient,
    ):
        self.task_queue_service = task_queue_service
        self.transfer_service = transfer_service
        self.whatsapp_client = whatsapp_client

    async def can_handle(self, context: MessageContext) -> bool:
        """
        Can handle if:
        - Has active queue
        - Has planner output
        - All tasks are collection_complete
        """
        if not context.has_active_queue or not context.planner_output:
            return False

        task_results = await self.task_queue_service.get_task_results(context.phone_number)
        all_tasks_ready = all(
            task_results.get(task.id, {}).get("status") == TaskStatus.COLLECTION_COMPLETE.value
            for task in context.planner_output.tasks
        )

        return all_tasks_ready

    async def handle(self, context: MessageContext) -> MessageContext:
        """Trigger batch authorization flow via WhatsApp PIN flow."""
        logger.debug("batch_authorization")

        redis = RedisClient.get_client()
        auth_sent_key = f"batch:auth_sent:{context.phone_number}"
        if await redis.get(auth_sent_key):
            return context

        if not context.planner_output:
            return context

        total_tasks = len(context.planner_output.tasks)

        timestamp = int(time.time())
        flow_token = f"batch-auth-{context.phone_number}-{timestamp}"

        await self.whatsapp_client.send_flow(
            to=context.phone_number,
            header="Authorize Transactions",
            flow_cta="Authorize",
            flow_id=settings.pin_confirmation_flow_id,
            screen_name="Pin",
            flow_token=flow_token,
            text_body=f"You have {total_tasks} transaction{'s' if total_tasks > 1 else ''} ready for authorization. Tap below to enter your PIN and process them all at once.",
            message_id=context.message_id,
        )

        await redis.setex(auth_sent_key, 300, "1")

        return context.with_response("", handled=True)
