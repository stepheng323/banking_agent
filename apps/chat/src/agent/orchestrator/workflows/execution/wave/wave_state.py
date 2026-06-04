"""Wave task bookkeeping helpers for the execution node."""

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.common import TERMINAL_STAGES
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import fail_task


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
        fail_task(state.tasks[task_id], reason)
    return stalled_task_ids


__all__ = [
    "_fail_stalled_wave_tasks",
    "_non_terminal_wave_task_ids",
]
