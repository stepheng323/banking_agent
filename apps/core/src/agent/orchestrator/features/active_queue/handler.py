"""Active queue handler - routes to current task in queue."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.services import TaskQueueService

if TYPE_CHECKING:
    from apps.core.src.agent.transfer import TransferService
    from apps.core.src.agent.airtime import AirtimeService


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
        print(f"🔍 [ActiveQueue] Routing to task: {context.current_task_id}")
        
        # Find the current task
        current_task = None
        for task in context.planner_output.tasks:
            if task.id == context.current_task_id:
                current_task = task
                break
        
        if not current_task:
            print(f"⚠️  [ActiveQueue] Task {context.current_task_id} not found in queue")
            return context
        
        # Route to appropriate executor
        if current_task.executor == "transfer":
            # Build classification dict with task_parameters
            classification_dict = {"intent": "transfer"}
            if current_task.parameters:
                classification_dict["task_parameters"] = current_task.parameters
            
            response = await self.transfer_service.run_simple(
                context.phone_number,
                context.text,
                classification_dict
            )
            return context.with_response(response, handled=True)
        
        elif current_task.executor == "airtime":
            # Build classification dict with task_parameters
            classification_dict = {"intent": "airtime"}
            if current_task.parameters:
                classification_dict["task_parameters"] = current_task.parameters
            
            response = await self.airtime_service.run_simple(
                context.phone_number,
                context.text,
                classification_dict
            )
            return context.with_response(response, handled=True)
        
        # Unknown executor
        print(f"⚠️  [ActiveQueue] Unknown executor: {current_task.executor}")
        return context
