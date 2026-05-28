"""Shared reconfirmation reset helpers for confirmation edits."""

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.utils.task_state import reset_tasks_to_extracted
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import TRANSACTION_INTENTS


def _reset_confirmation_tasks_for_reconfirm(tasks: dict[str, TaskSpec], task_ids: list[str]) -> None:
    reset_tasks_to_extracted(
        tasks,
        task_ids,
        copy_task=False,
        clear_idempotency=True,
    )
    for task_id in task_ids:
        task = tasks.get(task_id)
        if task is None or task.type not in TRANSACTION_INTENTS:
            continue
        task.payload["skip_extraction"] = True
        task.payload.pop("pending_user_message", None)
        task.payload.pop("confirmation_message_scoped", None)


__all__ = ["_reset_confirmation_tasks_for_reconfirm"]
