"""Restore previously removed confirmation tasks and rebuild for reconfirmation."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_edit_reconfirm import (
    _reset_confirmation_tasks_for_reconfirm,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_edit_targets import (
    _removed_task_from_entry,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _restore_wave_task(waves: list[list[str]], task_id: str, entry: dict[str, Any]) -> None:
    if any(task_id in wave for wave in waves):
        return

    wave_index = entry.get("wave_index")
    position = entry.get("position")
    if not isinstance(wave_index, int) or wave_index < 0:
        wave_index = 0
    while len(waves) <= wave_index:
        waves.append([])
    wave = waves[wave_index]
    if isinstance(position, int) and position >= 0:
        wave.insert(min(position, len(wave)), task_id)
    else:
        wave.append(task_id)


def restore_confirmation_tasks_and_reconfirm_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    task_ids_to_restore: list[str],
) -> dict[str, Any]:
    restore_set = {str(task_id) for task_id in task_ids_to_restore}
    if not restore_set:
        return {}

    state_view = interrupt_state_view(state)
    removed_tasks = dict(state_view.removed_confirmation_tasks)
    tasks = {task_id: task.model_copy(deep=True) for task_id, task in state_view.tasks.items()}
    task_results = dict(state_view.task_results)
    waves = [list(wave) for wave in state_view.waves]

    restored_task_ids: list[str] = []
    for task_id in restore_set:
        entry = removed_tasks.pop(task_id, None)
        if not isinstance(entry, dict):
            continue
        task = _removed_task_from_entry(entry)
        if task is None:
            continue
        tasks[task_id] = task.model_copy(deep=True)
        if "task_result" in entry:
            task_results[task_id] = entry.get("task_result")
        _restore_wave_task(waves, task_id, entry)
        restored_task_ids.append(task_id)

    if not restored_task_ids:
        return {}

    current_task_ids = [str(task_id) for task_id in getattr(interrupt, "task_ids", []) if str(task_id) in tasks]
    reconfirm_task_ids = list(dict.fromkeys([*current_task_ids, *restored_task_ids]))
    _reset_confirmation_tasks_for_reconfirm(tasks, reconfirm_task_ids)

    wave_order = {task_id: idx for wave in waves for idx, task_id in enumerate(wave)}
    next_interrupt_task_ids = sorted(reconfirm_task_ids, key=lambda task_id: wave_order.get(task_id, 10_000))
    last_interrupt = interrupt
    if hasattr(interrupt, "model_copy"):
        last_interrupt = interrupt.model_copy(update={"task_ids": next_interrupt_task_ids})

    logger.info(
        "confirmation_scoped_task_restore",
        restored_task_ids=restored_task_ids,
        active_task_ids=next_interrupt_task_ids,
    )
    return {
        "pending_interrupt": None,
        "last_interrupt": last_interrupt,
        "tasks": tasks,
        "task_results": task_results,
        "waves": [wave for wave in waves if wave],
        "current_wave_index": min(state_view.current_wave_index, max(len(waves) - 1, 0)),
        "removed_confirmation_tasks": removed_tasks,
        "pin_verified": False,
        "authorization_context": None,
        "last_callback": None,
    }


__all__ = [
    "_restore_wave_task",
    "restore_confirmation_tasks_and_reconfirm_updates",
]
