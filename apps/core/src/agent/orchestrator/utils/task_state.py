from typing import Any, Iterable

from apps.core.src.agent.orchestrator.models.domain import TaskStage


def set_tasks_cancelled(
    tasks: dict[str, Any],
    task_ids: Iterable[str],
    *,
    copy_task: bool,
) -> None:
    for task_id in task_ids:
        if task_id not in tasks:
            continue

        task = tasks[task_id].model_copy(deep=True) if copy_task else tasks[task_id]
        task.stage = TaskStage.CANCELLED
        tasks[task_id] = task


def reset_tasks_to_extracted(
    tasks: dict[str, Any],
    task_ids: Iterable[str],
    *,
    copy_task: bool,
    clear_idempotency: bool,
) -> None:
    for task_id in task_ids:
        if task_id not in tasks:
            continue

        task = tasks[task_id].model_copy(deep=True) if copy_task else tasks[task_id]
        task.stage = TaskStage.EXTRACTED
        task.payload["confirmation"] = {}
        if clear_idempotency:
            task.payload.pop("idempotency_key", None)
        tasks[task_id] = task

