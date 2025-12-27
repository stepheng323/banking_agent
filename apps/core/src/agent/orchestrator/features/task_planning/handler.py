"""Next task handler - triggers next task when current is None."""

from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from shared.utils.logging import get_logger

from .service import OrchestratorTaskPlanner

logger = get_logger(__name__)


class NextTaskHandler(MessageHandler):
    """
    Triggers next task when there's an active queue but no current task.

    This happens after a task completes and we need to move to the next one.
    """

    def __init__(self, task_planner: OrchestratorTaskPlanner):
        self.task_planner = task_planner

    async def can_handle(self, context: MessageContext) -> bool:
        """Can handle if there's an active queue but no current task."""
        return context.has_active_queue and context.current_task_id is None

    async def handle(self, context: MessageContext) -> MessageContext:
        """Trigger next task in queue."""
        logger.debug("triggering_next_task_in")

        response = await self.task_planner.handle_next_task(context.phone_number, context.text)

        if response:
            return context.with_response(response, handled=True)

        return context
