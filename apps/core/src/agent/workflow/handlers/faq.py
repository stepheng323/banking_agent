"""FAQ task handler.

Wraps FAQService for workflow execution.
"""

from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger

from apps.core.src.agent.workflow.context import WorkflowContext
from apps.core.src.agent.workflow.models import TaskResult
from apps.core.src.agent.workflow.handlers.base import success_result, failed_result

logger = get_logger(__name__)


class FAQTaskHandler:
    """Handler that executes FAQ lookups."""
    
    def __init__(self, faq_service):
        self.service = faq_service
    
    async def validate(self, task: PlannedTask, ctx: WorkflowContext) -> TaskResult | None:
        """FAQ doesn't require preflight validation."""
        return None
    
    async def execute(self, task: PlannedTask, ctx: WorkflowContext) -> TaskResult:
        """Execute FAQ lookup."""
        try:
            response = await self.service.run_simple(
                phone=ctx.phone_number,
                text=task.instruction,
                classification_result={"intent": "faq", "confidence": 1.0},
            )
            
            logger.info("faq_task_completed", task_id=task.task_id)
            
            return success_result(task.task_id, {"response": response})
            
        except Exception as e:
            logger.exception("faq_task_failed", task_id=task.task_id)
            return failed_result(task.task_id, str(e))
