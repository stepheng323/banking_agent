"""Account management task handler."""

from typing import TYPE_CHECKING

from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger

from ..models import ErrorKind, TaskResult
from ..workflow_context import WorkflowContext
from .base import BaseTaskHandler

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.account_management.service import AccountManagementService

logger = get_logger(__name__)


class AccountManagementHandler(BaseTaskHandler):
    """Handler for account management tasks."""

    async def execute(self, task: PlannedTask, context: WorkflowContext) -> TaskResult:
        """Execute account management using AccountManagementService."""
        try:
            account_service: AccountManagementService | None = context.get_service("account_management_service")
            if not account_service:
                return self._create_failure_result(task, "Account management service not available", ErrorKind.BUSINESS)

            # Get user context
            user_cache = context.get_service("user_cache")
            user_ctx = {}
            if user_cache:
                user_data = await user_cache.get_all(context.phone_number)
                accounts = user_data.get("accounts", []) if user_data else []
                user_ctx = {"accounts": accounts}

            # Execute task
            task_message = task.instruction or "show accounts"
            result = await account_service.run_simple(context.phone_number, task_message, user_context=user_ctx)

            # Send result to user if whatsapp client available
            if context.whatsapp_client and result:
                await context.whatsapp_client.send_text(context.phone_number, result)

            return self._create_success_result(
                task,
                data={
                    "result": result,
                    "action": task_message,
                },
            )

        except Exception as e:
            logger.error(f"[ACCOUNT_MGMT] Error: {e}", exc_info=True)
            return self._create_failure_result(task, str(e), ErrorKind.UNKNOWN)
