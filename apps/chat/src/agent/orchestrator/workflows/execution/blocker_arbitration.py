"""Wave-level blocker arbitration for execution turns."""

from dataclasses import dataclass
from typing import Literal

from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import existing_tasks, iter_tasks
from apps.chat.src.agent.orchestrator.workflows.execution.wave.wave_state import current_wave_index
from shared.utils.logging import get_logger

logger = get_logger(__name__)

BlockerKind = Literal["input", "confirmation", "auth", "none"]


@dataclass(frozen=True)
class BlockerDecision:
    """The single blocker the orchestrator should surface for a wave."""

    kind: BlockerKind
    task_ids: list[str]
    suppressed_counts: dict[str, int]
    blocker_counts: dict[str, int]
    current_wave_index: int
    current_wave: list[str]


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


def gate_task_ids(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    candidate_task_ids: list[str],
    stage: TaskStage,
) -> list[str]:
    """Resolve actionable confirmation/auth task ids for the current wave."""
    candidate_tasks = existing_tasks(state, candidate_task_ids)
    group_ids = {
        str(task.payload.get("async_group_id"))
        for _task_id, task in candidate_tasks
        if task.payload.get("async_group_id")
    }
    return _dedupe_task_ids(
        [
            *[task_id for task_id, _task in candidate_tasks],
            *[task_id for task_id, task in existing_tasks(state, current_wave) if task.stage == stage],
            *[
                task_id
                for task_id, task in iter_tasks(state)
                if task.stage == stage and group_ids and str(task.payload.get("async_group_id")) in group_ids
            ],
        ],
        current_wave,
    )


def choose_wave_blocker(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    agg: ExecutionAccumulator,
) -> BlockerDecision:
    """Choose the one blocker the orchestrator should surface for this wave."""
    input_task_ids = _dedupe_task_ids(agg.input_task_ids(), current_wave)
    confirmation_task_ids = gate_task_ids(
        state=state,
        current_wave=current_wave,
        candidate_task_ids=agg.confirmation_task_ids(),
        stage=TaskStage.AWAITING_CONFIRMATION,
    )
    auth_task_ids = gate_task_ids(
        state=state,
        current_wave=current_wave,
        candidate_task_ids=agg.auth_task_ids(),
        stage=TaskStage.AWAITING_AUTH,
    )

    blocker_counts = {
        "input": len(input_task_ids),
        "confirmation": len(confirmation_task_ids),
        "auth": len(auth_task_ids),
    }

    if input_task_ids:
        kind: BlockerKind = "input"
        task_ids = input_task_ids
        suppressed_counts = {key: count for key, count in blocker_counts.items() if key != "input" and count > 0}
    elif confirmation_task_ids:
        kind = "confirmation"
        task_ids = confirmation_task_ids
        suppressed_counts = {"auth": blocker_counts["auth"]} if blocker_counts["auth"] > 0 else {}
    elif auth_task_ids:
        kind = "auth"
        task_ids = auth_task_ids
        suppressed_counts = {}
    else:
        kind = "none"
        task_ids = []
        suppressed_counts = {}

    decision = BlockerDecision(
        kind=kind,
        task_ids=task_ids,
        suppressed_counts=suppressed_counts,
        blocker_counts=blocker_counts,
        current_wave_index=current_wave_index(state),
        current_wave=list(current_wave),
    )
    logger.info(
        "wave_blocker_arbitrated",
        selected_kind=decision.kind,
        selected_task_ids=decision.task_ids,
        current_wave_index=decision.current_wave_index,
        current_wave=decision.current_wave,
        blocker_counts=decision.blocker_counts,
        suppressed_counts=decision.suppressed_counts,
    )
    return decision


__all__ = ["BlockerDecision", "choose_wave_blocker", "gate_task_ids"]
