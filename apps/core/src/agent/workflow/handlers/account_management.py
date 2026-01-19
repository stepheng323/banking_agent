"""Account management task handler.

Wraps AccountManagementService for workflow execution.
"""

from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger

from apps.core.src.agent.workflow.context import WorkflowContext
from apps.core.src.agent.workflow.models import TaskResult
from apps.core.src.agent.workflow.handlers.base import success_result, failed_result

logger = get_logger(__name__)


class AccountManagementTaskHandler:
    """Handler that executes account management tasks."""
    
    def __init__(self, account_management_service):
        self.service = account_management_service
    
    async def validate(self, task: PlannedTask, ctx: WorkflowContext) -> TaskResult | None:
        """Account management doesn't require preflight validation."""
        return None
    
    async def execute(self, task: PlannedTask, ctx: WorkflowContext) -> TaskResult:
        """Execute account management action."""
        try:
            response = await self.service.run_simple(
                phone=ctx.phone_number,
                text=task.instruction,
                classification_result={"intent": "account_management", "confidence": 1.0},
            )
            
            logger.info("account_management_task_completed", task_id=task.task_id)
            
            return success_result(task.task_id, {"response": response})
            
        except Exception as e:
            logger.exception("account_management_task_failed", task_id=task.task_id)
            return failed_result(task.task_id, str(e))
