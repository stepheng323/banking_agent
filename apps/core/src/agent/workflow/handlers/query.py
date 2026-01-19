"""Query task handler.

Wraps QueryService for workflow execution.
"""

from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger

from apps.core.src.agent.workflow.context import WorkflowContext
from apps.core.src.agent.workflow.models import TaskResult
from apps.core.src.agent.workflow.handlers.base import success_result, failed_result

logger = get_logger(__name__)


class QueryTaskHandler:
    """Handler that executes query tasks via QueryService."""
    
    def __init__(self, query_service):
        """
        Args:
            query_service: QueryService instance
        """
        self.service = query_service
    
    async def validate(self, task: PlannedTask, ctx: WorkflowContext) -> TaskResult | None:
        """
        Queries don't need preflight - they just run.
        """
        return None
    
    async def execute(self, task: PlannedTask, ctx: WorkflowContext) -> TaskResult:
        """Execute query via QueryService."""
        try:
            response = await self.service.run_simple(
                phone=ctx.phone_number,
                text=task.instruction,
                classification_result={"intent": "query", "confidence": 1.0},
            )
            
            logger.info("query_task_completed", task_id=task.task_id)
            
            return success_result(
                task.task_id,
                {"response": response},
            )
            
        except Exception as e:
            logger.exception("query_task_failed", task_id=task.task_id)
            return failed_result(task.task_id, str(e))
