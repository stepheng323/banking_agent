from typing import Any

from apps.chat.src.agent.orchestrator.workflows.planner.context.context_read_constants import TRANSACTION_EXECUTORS
from banking.policy.transaction_limits import MAX_TRANSACTION_BATCH_TASKS
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _transaction_batch_limit_message(*, transaction_count: int) -> str:
    return (
        f"I can handle up to {MAX_TRANSACTION_BATCH_TASKS} transactions in one batch. "
        f"You asked for {transaction_count}. Please send the first {MAX_TRANSACTION_BATCH_TASKS} now, "
        "then I can help with the rest."
    )


def _transaction_batch_limit_updates(
    *,
    planner_output: Any,
    capability_policy_notice: str | None,
) -> dict[str, Any] | None:
    transaction_task_count = sum(
        1 for task in planner_output.tasks if getattr(task, "executor", None) in TRANSACTION_EXECUTORS
    )
    if transaction_task_count <= MAX_TRANSACTION_BATCH_TASKS:
        return None

    logger.info(
        "planner_transaction_batch_limit_blocked",
        transaction_task_count=transaction_task_count,
        max_transaction_batch_tasks=MAX_TRANSACTION_BATCH_TASKS,
    )
    return {
        "planner_output": planner_output,
        "new_tasks": {},
        "waves": [],
        "stashed_query_session_update": None,
        "batch_limit_response": _transaction_batch_limit_message(transaction_count=transaction_task_count),
        "capability_policy_notice": capability_policy_notice,
    }


__all__ = ["_transaction_batch_limit_message", "_transaction_batch_limit_updates"]
