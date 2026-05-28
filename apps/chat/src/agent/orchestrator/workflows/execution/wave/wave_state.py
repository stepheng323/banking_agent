"""Wave task bookkeeping helpers for the execution node."""

from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.common import TERMINAL_STAGES


def _dedupe_task_ids(task_ids: list[str], current_wave: list[str]) -> list[str]:
    """Deduplicate task ids while preserving current-wave order."""
    requested = [task_id for task_id in task_ids if task_id]
    if not requested:
        return []

    requested_set = set(requested)
    seen: set[str] = set()
    ordered: list[str] = []

    for task_id in current_wave:
        if task_id in requested_set and task_id not in seen:
            ordered.append(task_id)
            seen.add(task_id)

    for task_id in requested:
        if task_id not in seen:
            ordered.append(task_id)
            seen.add(task_id)

    return ordered


def _gate_task_ids(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    candidate_task_ids: list[str],
    stage: TaskStage,
) -> list[str]:
    group_ids = {
        str(state.tasks[task_id].payload.get("async_group_id"))
        for task_id in candidate_task_ids
        if task_id in state.tasks and state.tasks[task_id].payload.get("async_group_id")
    }
    return _dedupe_task_ids(
        [
            *[task_id for task_id in candidate_task_ids if task_id in state.tasks],
            *[task_id for task_id in current_wave if task_id in state.tasks and state.tasks[task_id].stage == stage],
            *[
                task_id
                for task_id, task in state.tasks.items()
                if task.stage == stage and group_ids and str(task.payload.get("async_group_id")) in group_ids
            ],
        ],
        current_wave,
    )


def _non_terminal_wave_task_ids(state: OrchestratorState, current_wave: list[str]) -> list[str]:
    return [
        task_id
        for task_id in current_wave
        if (task := state.tasks.get(task_id)) is not None and task.stage not in TERMINAL_STAGES
    ]


def _fail_stalled_wave_tasks(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    reason: str,
) -> list[str]:
    stalled_task_ids = _non_terminal_wave_task_ids(state, current_wave)
    for task_id in stalled_task_ids:
        task = state.tasks[task_id]
        task.stage = TaskStage.FAILED
        task.payload["error"] = reason
    return stalled_task_ids


__all__ = [
    "_dedupe_task_ids",
    "_fail_stalled_wave_tasks",
    "_gate_task_ids",
    "_non_terminal_wave_task_ids",
]
