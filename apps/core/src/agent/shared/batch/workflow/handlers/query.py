"""Query task handler for balance and account queries."""

from __future__ import annotations

from apps.core.src.agent.graphs.query import QueryWorker
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome
from apps.core.src.messaging.outbox import enqueue_outbox_say
from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger

from ..models import ErrorKind, TaskResult
from ..workflow_context import WorkflowContext
from .base import BaseTaskHandler

logger = get_logger(__name__)


class QueryHandler(BaseTaskHandler):
    """Handler for query tasks using QueryService."""

    async def execute(self, task: PlannedTask, context: WorkflowContext) -> TaskResult:
        """Execute query using QueryService."""
        try:
            query_service: QueryWorker | None = context.get_service("query_service")
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

            # Prepare context for QueryWorker
            worker_payload = {"message": query_message}
            worker_context = {
                "phone_number": context.phone_number,
                "accounts": user_ctx.get("accounts", []),
            }

            result = await query_service.run(worker_payload, worker_context)

            # Send result to user if whatsapp client available
            if context.queue and result and result.outcome == TransactionOutcome.OK:
                # QueryWorker returns a TransactionResult. The legacy code expected a string/simple result?
                # Actually run() returns TransactionResult.
                # If result.patch has 'summary_text', use that.
                response_text = (
                    result.patch.get("summary_text", "Here is your query result.") if result.patch else "Done."
                )

                await enqueue_outbox_say(
                    context.queue,
                    context.phone_number,
                    "whatsapp",
                    response_text,
                    metadata={"source": "batch_query_handler"},
                )

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
