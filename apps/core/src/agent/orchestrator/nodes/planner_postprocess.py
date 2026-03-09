"""Planner task post-processing helpers."""

from typing import Any

from apps.core.src.agent.orchestrator.models.domain import TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner_fastpath import TRANSACTION_EXECUTORS
from apps.core.src.agent.orchestrator.utils.task_payload import _derive_recipients_from_user_text
from shared.types.planner import PlannedTask


def _next_transfer_fanout_task_id(base_task_id: str, index: int, existing_ids: set[str]) -> str:
    candidate = f"{base_task_id}_r{index}"
    while candidate in existing_ids:
        index += 1
        candidate = f"{base_task_id}_r{index}"
    existing_ids.add(candidate)
    return candidate


def _expand_underproduced_transfer_tasks(
    planned_tasks: list[PlannedTask],
    user_text: str,
) -> tuple[list[PlannedTask], dict[str, Any] | None]:
    """Fan out a single transfer task when user text clearly contains multiple recipients."""
    transfer_indices = [idx for idx, task in enumerate(planned_tasks) if task.executor == "transfer"]
    if len(transfer_indices) != 1:
        return planned_tasks, None

    recipients = _derive_recipients_from_user_text(user_text)
    if len(recipients) < 2:
        return planned_tasks, None

    source_index = transfer_indices[0]
    source_task = planned_tasks[source_index]
    source_parameters = source_task.parameters

    # Keep parser repair narrow: avoid fanout when task is account+bank explicit or purely reference-based.
    if source_parameters.recipient_account or source_parameters.bank_name:
        return planned_tasks, None
    if source_parameters.reference and not (source_parameters.recipient or source_parameters.recipient_name):
        return planned_tasks, None

    existing_ids = {task.task_id for task in planned_tasks}
    expanded_task_ids: list[str] = [source_task.task_id]
    expanded_source_tasks: list[PlannedTask] = []

    first_task = source_task.model_copy(deep=True)
    first_task.parameters.recipient = recipients[0]
    first_task.parameters.recipient_name = recipients[0]
    expanded_source_tasks.append(first_task)

    for idx, recipient in enumerate(recipients[1:], start=2):
        clone = source_task.model_copy(deep=True)
        clone.task_id = _next_transfer_fanout_task_id(source_task.task_id, idx, existing_ids)
        clone.parameters.recipient = recipient
        clone.parameters.recipient_name = recipient
        expanded_source_tasks.append(clone)
        expanded_task_ids.append(clone.task_id)

    expanded_tasks: list[PlannedTask] = []
    for idx, task in enumerate(planned_tasks):
        if idx == source_index:
            expanded_tasks.extend(expanded_source_tasks)
            continue

        copy_task = task.model_copy(deep=True)
        if source_task.task_id in copy_task.depends_on:
            rewritten: list[str] = []
            for dep in copy_task.depends_on:
                if dep == source_task.task_id:
                    rewritten.extend(expanded_task_ids)
                else:
                    rewritten.append(dep)
            copy_task.depends_on = list(dict.fromkeys(rewritten))
        expanded_tasks.append(copy_task)

    return (
        expanded_tasks,
        {
            "source_task_id": source_task.task_id,
            "recipient_count": len(expanded_task_ids),
            "recipient_names": recipients,
        },
    )


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
    active_tasks = [state.tasks.get(task_id) for task_id in current_wave if state.tasks.get(task_id) is not None]
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


__all__ = [
    "_expand_underproduced_transfer_tasks",
    "_should_replan_active_wave",
    "_strip_transactional_depends_on_edges",
]
