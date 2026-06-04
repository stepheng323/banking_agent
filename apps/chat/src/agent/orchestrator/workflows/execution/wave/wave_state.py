"""Wave task bookkeeping helpers for the execution node."""

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import non_terminal_task_ids, non_terminal_tasks
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import fail_task


def _non_terminal_wave_task_ids(state: OrchestratorState, current_wave: list[str]) -> list[str]:
    return non_terminal_task_ids(state, current_wave)


def _fail_stalled_wave_tasks(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    reason: str,
) -> list[str]:
    stalled_tasks = non_terminal_tasks(state, current_wave)
    for _task_id, task in stalled_tasks:
        fail_task(task, reason)
    return [task_id for task_id, _task in stalled_tasks]


__all__ = [
    "_fail_stalled_wave_tasks",
    "_non_terminal_wave_task_ids",
]
