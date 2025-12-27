"""Active queue handler - routes to current task in queue."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.services import TaskQueueService
from shared.utils.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from apps.core.src.agent.sub_agents.airtime import AirtimeService
    from apps.core.src.agent.sub_agents.transfer import TransferService


class ActiveQueueHandler(MessageHandler):
    """
    Routes message to current task in active queue.

    Runs when there's an active queue with a current task.
    """

    def __init__(
        self,
        task_queue_service: TaskQueueService,
        transfer_service: "TransferService",
        airtime_service: "AirtimeService",
    ):
        self.task_queue_service = task_queue_service
        self.transfer_service = transfer_service
        self.airtime_service = airtime_service

    async def can_handle(self, context: MessageContext) -> bool:
        """Can handle if there's an active queue with a current task."""
        return context.has_active_queue and context.current_task_id is not None

    async def handle(self, context: MessageContext) -> MessageContext:
        """Route to current task."""
        logger.debug("routing_to")

        # Find the current task
        current_task = None
        for task in context.planner_output.tasks:
            if task.id == context.current_task_id:
                current_task = task
                break

        if not current_task:
            logger.warning("task_not_found")
            return context

        # Route to appropriate executor
        if current_task.executor == "transfer":
            # Build classification dict with task_parameters
            classification_dict = {"intent": "transfer"}
            if current_task.parameters:
                classification_dict["task_parameters"] = current_task.parameters

            response = await self.transfer_service.run_simple(
                context.phone_number, context.text, classification_dict
            )
            return context.with_response(response, handled=True)

        elif current_task.executor == "airtime":
            # Build classification dict with task_parameters
            classification_dict = {"intent": "airtime"}
            if current_task.parameters:
                classification_dict["task_parameters"] = current_task.parameters

            response = await self.airtime_service.run_simple(
                context.phone_number, context.text, classification_dict
            )
            return context.with_response(response, handled=True)

        # Unknown executor
        logger.warning("unknown")
        return context
