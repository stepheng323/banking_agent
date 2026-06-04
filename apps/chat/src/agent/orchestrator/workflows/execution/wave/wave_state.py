"""Wave task bookkeeping helpers for the execution node."""

from dataclasses import dataclass

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import non_terminal_task_ids, non_terminal_tasks
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import fail_task


@dataclass(frozen=True)
class ExecutionWavePosition:
    """Read-only typed facade over execution wave position."""

    waves: list[list[str]]
    index: int

    @property
    def wave_count(self) -> int:
        return len(self.waves)

    @property
    def has_current_wave(self) -> bool:
        return bool(self.waves) and self.index < self.wave_count

    @property
    def current_wave(self) -> list[str]:
        if not self.has_current_wave:
            return []
        return self.waves[self.index]

    @property
    def next_index(self) -> int:
        return self.index + 1


def wave_position(state: OrchestratorState) -> ExecutionWavePosition:
    return ExecutionWavePosition(waves=state.waves, index=state.current_wave_index)


def current_wave_index(state: OrchestratorState) -> int:
    return wave_position(state).index


def next_wave_index(state: OrchestratorState) -> int:
    return wave_position(state).next_index


def wave_list(state: OrchestratorState) -> list[list[str]]:
    return wave_position(state).waves


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
    "ExecutionWavePosition",
    "current_wave_index",
    "_fail_stalled_wave_tasks",
    "_non_terminal_wave_task_ids",
    "next_wave_index",
    "wave_list",
    "wave_position",
]
