"""Remove selected confirmation tasks and rebuild the flow for reconfirmation."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_edit_reconfirm import (
    _reset_confirmation_tasks_for_reconfirm,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import InterruptStateView, interrupt_state_view
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _removed_task_entry(
    *,
    state_view: InterruptStateView,
    task_id: str,
    task: TaskSpec,
) -> dict[str, Any]:
    wave_index = None
    position = None
    for idx, wave in enumerate(state_view.waves):
        if task_id in wave:
            wave_index = idx
            position = wave.index(task_id)
            break

    return {
        "task": task.model_copy(deep=True),
        "wave_index": wave_index,
        "position": position,
        "task_result": state_view.task_result(task_id),
    }


def remove_confirmation_tasks_and_reconfirm_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    task_ids_to_remove: list[str],
) -> dict[str, Any]:
    remove_set = {str(task_id) for task_id in task_ids_to_remove}
    if not remove_set:
        return {}

    state_view = interrupt_state_view(state)
    removed_tasks = dict(state_view.removed_confirmation_tasks)
    for task_id in remove_set:
        task = state_view.task(task_id)
        if task is None:
            continue
        removed_tasks[task_id] = _removed_task_entry(state_view=state_view, task_id=task_id, task=task)

    tasks = {
        task_id: task.model_copy(deep=True) for task_id, task in state_view.tasks.items() if task_id not in remove_set
    }
    task_results = {task_id: result for task_id, result in state_view.task_results.items() if task_id not in remove_set}
    waves = [[task_id for task_id in wave if task_id not in remove_set] for wave in state_view.waves]
    waves = [wave for wave in waves if wave]
    current_wave_index = min(state_view.current_wave_index, max(len(waves) - 1, 0))

    remaining_interrupt_task_ids = [
        str(task_id)
        for task_id in getattr(interrupt, "task_ids", [])
        if str(task_id) in tasks and str(task_id) not in remove_set
    ]
    _reset_confirmation_tasks_for_reconfirm(tasks, remaining_interrupt_task_ids)

    last_interrupt = interrupt
    if hasattr(interrupt, "model_copy"):
        last_interrupt = interrupt.model_copy(update={"task_ids": remaining_interrupt_task_ids})

    logger.info(
        "confirmation_scoped_task_removal",
        removed_task_ids=sorted(remove_set),
        remaining_task_ids=remaining_interrupt_task_ids,
    )
    return {
        "pending_interrupt": None,
        "last_interrupt": last_interrupt,
        "tasks": tasks,
        "task_results": task_results,
        "waves": waves,
        "current_wave_index": current_wave_index,
        "removed_confirmation_tasks": removed_tasks,
        "pin_verified": False,
        "authorization_context": None,
        "last_callback": None,
    }


__all__ = [
    "_removed_task_entry",
    "remove_confirmation_tasks_and_reconfirm_updates",
]
