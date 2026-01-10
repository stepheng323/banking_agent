"""Task queue handler - manages active queue routing."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.pipeline_stages.intent_routing.handlers.base import IntentHandler
from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.graphs.airtime import AirtimeService
from apps.core.src.agent.graphs.transfer import TransferService
from shared.services.task_queue import TaskQueueService
from shared.utils.logging import get_logger

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.registry import ExecutorRegistry
    from apps.core.src.agent.orchestrator.pipeline_stages.task_queue.planner import (
        OrchestratorTaskPlanner,
    )
    from apps.core.src.agent.orchestrator.pipeline import RoutingContextService
    from apps.core.src.agent.graphs.transfer import TransferService

logger = get_logger(__name__)


class TaskQueueHandler(MessageHandler):
    """Routes to current task or triggers next task in queue."""

    def __init__(
        self,
        task_queue_service: TaskQueueService,
        task_planner: "OrchestratorTaskPlanner",
        transfer_service: "TransferService",
        airtime_service: "AirtimeService",
        registry: "ExecutorRegistry | None" = None,
    ) -> None:
        super().__init__()
        self.task_queue_service = task_queue_service
        self.task_planner = task_planner
        self.transfer_service = transfer_service
        self.airtime_service = airtime_service
        self.registry = registry

    async def can_handle(self, context: MessageContext) -> bool:
        """Can handle if there's an active queue."""
        return context.has_active_queue

    async def handle(self, context: MessageContext) -> MessageContext:
        """Route to current task or trigger next task."""
        if context.current_task_id is not None:
            return await self._route_to_current_task(context)
        return await self._trigger_next_task(context)
    async def handle(self, ctx: "RoutingContext") -> str:
        current_task = await self.task_queue_service.get_current_task(ctx.phone_number)
        
        # If no active task, try to get next one
        if not current_task:
            task_result = await self.task_planner.handle_next_task(ctx.phone_number, ctx.text)
            if task_result:
                return task_result
            # Fallback to intent routing if no tasks
            return await self.handlers[0].handle(ctx)

        # Execute current task
        executor_name = current_task.get("executor")
        if not executor_name:
             logger.error("missing_executor_name", task_id=current_task.get("id"))
             return "I'm having trouble processing that request."

        # Use registry if available, fallback to manual check (during migration)
        if self.registry:
            executor = self.registry.get(executor_name)
            if executor:
                return await executor.run_simple(
                    ctx.phone_number,
                    ctx.text,
                    {"intent": executor_name, "confidence": 1.0, "is_cancellation": False},
                    image_data=ctx.image_data,
                )
        
        # Legacy fallback (temporary)
        if executor_name == "transfer":
            return await self.transfer_service.run_simple(
                ctx.phone_number,
                ctx.text,
                {"intent": "transfer", "confidence": 1.0, "is_cancellation": False},
                image_data=ctx.image_data,
            )
        elif executor_name == "airtime":
            return await self.airtime_service.run_simple(
                ctx.phone_number,
                ctx.text,
                {"intent": "airtime", "confidence": 1.0, "is_cancellation": False},
                image_data=ctx.image_data,
            )
            
        return "I'm not sure how to handle that task."

    async def _route_to_current_task(self, context: MessageContext) -> MessageContext:
        """Route message to current task in queue."""
        logger.debug("routing_to_current_task")

        current_task = None
        for task in context.planner_output.tasks:
            if task.id == context.current_task_id:
                current_task = task
                break

        if not current_task:
            logger.warning("task_not_found")
            return context

        if current_task.executor == "transfer":
            classification_dict = {"intent": "transfer"}
            if current_task.parameters:
                classification_dict["task_parameters"] = current_task.parameters
            response = await self.transfer_service.run_simple(
                context.phone_number, context.text, classification_dict
            )
            return context.with_response(response, handled=True)

        if current_task.executor == "airtime":
            classification_dict = {"intent": "airtime"}
            if current_task.parameters:
                classification_dict["task_parameters"] = current_task.parameters
            response = await self.airtime_service.run_simple(
                context.phone_number, context.text, classification_dict
            )
            return context.with_response(response, handled=True)

        logger.warning("unknown_executor", executor=current_task.executor)
        return context

    async def _trigger_next_task(self, context: MessageContext) -> MessageContext:
        """Trigger next task in queue."""
        logger.debug("triggering_next_task")
        response = await self.task_planner.handle_next_task(
            context.phone_number, context.text
        )
        if response:
            return context.with_response(response, handled=True)
        return context
