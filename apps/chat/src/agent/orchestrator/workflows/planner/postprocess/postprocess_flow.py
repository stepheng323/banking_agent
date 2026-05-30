"""Planner post-processing flow-level helpers."""

from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_read_constants import TRANSACTION_EXECUTORS
from shared.types.planner import PlannedTask


def _should_replan_active_wave(state: OrchestratorState) -> bool:
    """Allow replanning active waves only for explicit pre-execution update turns."""
    if not state.waves or state.pending_interrupt is not None:
        return False
    if not (state.last_message_text or "").strip():
        return False
    if state.current_wave_index >= len(state.waves):
        return False

    current_wave = state.waves[state.current_wave_index]
    if not current_wave:
        return False

    replannable_stages = {TaskStage.AWAITING_CONFIRMATION, TaskStage.AWAITING_AUTH}
    active_tasks = [task for task_id in current_wave if (task := state.tasks.get(task_id)) is not None]
    if not active_tasks:
        return False
    return all(task.stage in replannable_stages for task in active_tasks)


def _strip_transactional_depends_on_edges(
    planned_tasks: list[PlannedTask],
) -> tuple[list[PlannedTask], list[tuple[str, str]]]:
    """Remove depends_on edges between transaction tasks for single-batch auth collection."""
    executor_by_task_id = {task.task_id: task.executor for task in planned_tasks}
    stripped_edges: list[tuple[str, str]] = []
    normalized_tasks: list[PlannedTask] = []

    for task in planned_tasks:
        copy_task = task.model_copy(deep=True)
        if copy_task.executor not in TRANSACTION_EXECUTORS:
            normalized_tasks.append(copy_task)
            continue

        next_depends_on: list[str] = []
        seen: set[str] = set()
        for dep_task_id in copy_task.depends_on:
            dep_executor = executor_by_task_id.get(dep_task_id)
            if dep_executor in TRANSACTION_EXECUTORS:
                stripped_edges.append((dep_task_id, copy_task.task_id))
                continue
            if dep_task_id in seen:
                continue
            seen.add(dep_task_id)
            next_depends_on.append(dep_task_id)
        copy_task.depends_on = next_depends_on
        normalized_tasks.append(copy_task)

    return normalized_tasks, stripped_edges


__all__ = ["_should_replan_active_wave", "_strip_transactional_depends_on_edges"]
