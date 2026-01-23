"""Account task handler."""

from typing import TYPE_CHECKING

from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger

from ..models import ErrorKind, TaskResult
from ..workflow_context import WorkflowContext
from .base import BaseTaskHandler

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.account.service import AccountService

logger = get_logger(__name__)


class AccountHandler(BaseTaskHandler):
    """Handler for account tasks."""

    async def execute(self, task: PlannedTask, context: WorkflowContext) -> TaskResult:
        try:
            account_service: AccountService | None = context.get_service("account_service")
            if not account_service or not getattr(account_service, "worker", None):
                return self._create_failure_result(task, "Account service or worker not available", ErrorKind.BUSINESS)

            worker = account_service.worker

            user_cache = context.get_service("user_cache")
            user_ctx = {}
            if user_cache:
                user_data = await user_cache.get_all(context.phone_number)
                accounts = user_data.get("accounts", []) if user_data else []
                user_ctx = {"accounts": accounts}

            result = await worker.run(
                payload=task.parameters.model_dump() if task.parameters else {},
                context=user_ctx,
                user_message=task.instruction,
            )

            response = result.response or ""

            if context.whatsapp_client and result:
                await context.whatsapp_client.send_text(context.phone_number, result)

            return self._create_success_result(
                task,
                data={
                    "result": response,
                    "action": task.instruction or "list",
                },
            )

        except Exception as e:
            logger.error(f"[ACCOUNT] Error: {e}", exc_info=True)
            return self._create_failure_result(task, str(e), ErrorKind.UNKNOWN)
