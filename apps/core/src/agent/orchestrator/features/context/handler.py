"""Context loader handler - loads user context and queue state."""

import json
from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from .service import OrchestratorContextManager
from apps.core.src.agent.services import TaskQueueService


class ContextLoaderHandler(MessageHandler):
    """
    Loads user context, conversation state, and queue information.
    
    This handler always runs first to populate the context with necessary data.
    """
    
    def __init__(
        self,
        context_manager: OrchestratorContextManager,
        task_queue_service: TaskQueueService,
    ):
        self.context_manager = context_manager
        self.task_queue_service = task_queue_service
    
    async def can_handle(self, context: MessageContext) -> bool:
        """Always runs to load context."""
        return True
    
    async def handle(self, context: MessageContext) -> MessageContext:
        """Load user context and queue state."""
        # Load user context
        user_ctx = await self.context_manager.load_user_context(context.phone_number)
        conversation_state = user_ctx.get("conversation_state")
        last_response = user_ctx.get("last_response")
        suggestion_data = user_ctx.get("suggestion_data")
        
        # Parse suggestion context
        suggestion_context = None
        if suggestion_data:
            try:
                suggestion_context = json.loads(suggestion_data)
            except (json.JSONDecodeError, TypeError):
                suggestion_context = None
        
        # Check for active queue
        has_active_queue = await self.task_queue_service.has_active_queue(context.phone_number)
        
        # Get planner output if queue exists
        planner_output = None
        current_task_id = None
        if has_active_queue:
            planner_output = await self.task_queue_service.get_task_queue(context.phone_number)
            if planner_output:
                current_task_id = await self.task_queue_service.get_current_task(context.phone_number)
        
        # Return updated context
        return context.update(
            user_context=user_ctx,
            conversation_state=conversation_state,
            last_response=last_response,
            suggestion_data=suggestion_data,
            suggestion_context=suggestion_context,
            has_active_queue=has_active_queue,
            planner_output=planner_output,
            current_task_id=current_task_id,
        )
