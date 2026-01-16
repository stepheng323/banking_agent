"""Query task handler for balance and account queries."""

from typing import TYPE_CHECKING

from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger

from ..models import ErrorKind, TaskResult
from ..workflow_context import WorkflowContext
from .base import BaseTaskHandler

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.query import QueryService

logger = get_logger(__name__)


class QueryHandler(BaseTaskHandler):
    """Handler for query tasks using QueryService."""

    async def execute(self, task: PlannedTask, context: WorkflowContext) -> TaskResult:
        """Execute query using QueryService."""
        try:
            query_service: QueryService | None = context.get_service("query_service")
            if not query_service:
                return self._create_failure_result(task, "Query service not available", ErrorKind.BUSINESS)

            # Get user context
            user_cache = context.get_service("user_cache")
            user_ctx = {}
            if user_cache:
                user_data = await user_cache.get_all(context.phone_number)
                accounts = user_data.get("accounts", []) if user_data else []
                user_ctx = {"accounts": accounts}

            # Execute query
            query_message = task.instruction or "show balance"
            result = await query_service.run_simple(context.phone_number, query_message, user_context=user_ctx)

            # Send result to user if whatsapp client available
            if context.whatsapp_client and result:
                await context.whatsapp_client.send_text(context.phone_number, result)

            return self._create_success_result(
                task,
                data={
                    "result": result,
                    "query": query_message,
                },
            )

        except Exception as e:
            logger.error(f"[QUERY] Error: {e}", exc_info=True)
            return self._create_failure_result(task, str(e), ErrorKind.UNKNOWN)
