"""Batch authorization handler - handles batch authorization confirmation."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.services import TaskQueueService

if TYPE_CHECKING:
    from apps.core.src.agent.transfer import TransferService
from shared.types.agent_types import TaskStatus


class BatchAuthorizationHandler(MessageHandler):
    """
    Handles batch authorization confirmation.
    
    Runs when all tasks are collection_complete and user confirms.
    """
    
    CONFIRMATION_INTENTS = {"yes", "confirm", "proceed", "ok"}
    CONFIRMATION_TEXTS = {"yes", "confirm", "proceed", "ok", "y"}
    
    def __init__(
        self,
        task_queue_service: TaskQueueService,
        transfer_service: "TransferService",
    ):
        self.task_queue_service = task_queue_service
        self.transfer_service = transfer_service
    
    async def can_handle(self, context: MessageContext) -> bool:
        """
        Can handle if:
        - Has active queue
        - Has planner output
        - All tasks are collection_complete
        - User is confirming
        """
        if not context.has_active_queue or not context.planner_output:
            return False
        
        # Check if all tasks are collection_complete
        task_results = await self.task_queue_service.get_task_results(context.phone_number)
        all_tasks_ready = all(
            task_results.get(task.id, {}).get("status") == TaskStatus.COLLECTION_COMPLETE.value
            for task in context.planner_output.tasks
        )
        
        if not all_tasks_ready:
            return False
        
        # Check if user is confirming
        is_confirmation = (
            context.intent in self.CONFIRMATION_INTENTS or
            context.text.lower().strip() in self.CONFIRMATION_TEXTS
        )
        
        return is_confirmation
    
    async def handle(self, context: MessageContext) -> MessageContext:
        """Trigger batch authorization flow."""
        print(f"🔍 [BatchAuth] Triggering batch authorization")
        
        # Get task results
        task_results = await self.task_queue_service.get_task_results(context.phone_number)
        
        # Find first collection_complete task
        first_task = None
        for task in context.planner_output.tasks:
            if task_results.get(task.id, {}).get("status") == TaskStatus.COLLECTION_COMPLETE.value:
                first_task = task
                break
        
        if first_task and first_task.executor == "transfer":
            # Set as current task and trigger authorization
            await self.task_queue_service.set_current_task(context.phone_number, first_task.id)
            
            # Trigger authorization flow
            auth_response = await self.transfer_service.run_simple(
                context.phone_number, "authorize", {"intent": "transfer"}
            )
            return context.with_response(auth_response, handled=True)
        
        # Fallback response
        return context.with_response(
            "Ready to authorize. Please proceed with the authorization flow.",
            handled=True
        )
