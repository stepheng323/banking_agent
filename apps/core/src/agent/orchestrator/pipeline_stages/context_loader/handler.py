"""Context loader handler - loads user context and queue state."""

import json

from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.services import TaskQueueService

from apps.core.src.agent.orchestrator.pipeline_stages.context_loader.service import OrchestratorContextManager


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
        (
            user_ctx,
            conversation_state,
            last_response,
            suggestion_data,
        ) = await self.context_manager.load_context_parallel(context.phone_number)

        suggestion_context = None
        if suggestion_data:
            try:
                suggestion_context = json.loads(suggestion_data)
            except (json.JSONDecodeError, TypeError):
                suggestion_context = None

        has_active_queue = await self.task_queue_service.has_active_queue(context.phone_number)

        planner_output = None
        current_task_id = None
        if has_active_queue:
            planner_output = await self.task_queue_service.get_task_queue(context.phone_number)
            if planner_output:
                current_task_id = await self.task_queue_service.get_current_task(
                    context.phone_number
                )

        recent_transactions = await self.context_manager.get_recent_transactions(
            context.phone_number, limit=5
        )
        user_ctx["recent_transactions"] = recent_transactions

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
