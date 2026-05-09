import re
from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.interrupt.confirmation_resolve import (
    _message_targets_confirmation_task,
    _parse_scoped_confirmation_amount,
    _transfer_task_amount,
    _transfer_task_reference_matches,
)
from apps.chat.src.agent.orchestrator.utils.task_state import reset_tasks_to_extracted
from shared.utils.logging import get_logger

logger = get_logger(__name__)

TRANSACTION_INTENTS = {"transfer", "airtime", "data"}
_SCOPED_TASK_AMOUNT_RE = re.compile(
    r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?",
    re.IGNORECASE,
)
_MULTI_TARGET_REFERENCE_RE = re.compile(r"\b(all|every|both|each)\b|[,;]|\b(and|plus|also)\b", re.IGNORECASE)


def _task_matches_reference_segment(segment: str, task: TaskSpec) -> bool:
    if _message_targets_confirmation_task(segment, task):
        return True
    return task.type == "transfer" and _transfer_task_reference_matches(segment, task)


def _target_task_ids_from_decision(
    *,
    decision: Any,
    task_ids: list[str],
    state: OrchestratorState,
    removed: bool = False,
) -> list[str]:
    explicit_ids = [
        str(task_id)
        for task_id in getattr(decision, "target_task_ids", [])
        if str(task_id) in task_ids
    ]
    if explicit_ids:
        return list(dict.fromkeys(explicit_ids))

    target_types = {
        str(task_type).strip().lower()
        for task_type in getattr(decision, "target_types", [])
        if str(task_type).strip().lower() in TRANSACTION_INTENTS
    }
    if target_types:
        return [
            task_id
            for task_id in task_ids
            if (task := _task_for_target_id(state, task_id, removed=removed)) is not None
            and task.type in target_types
        ]

    matched_task_ids: list[str] = []
    for segment in _decision_target_texts(decision):
        segment_matches = [
            task_id
            for task_id in task_ids
            if (task := _task_for_target_id(state, task_id, removed=removed)) is not None
            and (segment.strip().lower() == task_id.lower() or _task_matches_reference_segment(segment, task))
        ]
        if len(segment_matches) == 1:
            matched_task_ids.extend(segment_matches)
            continue
        amount_matches = _task_ids_matching_amount_reference(
            reference=segment,
            task_ids=task_ids,
            state=state,
            removed=removed,
        )
        if len(amount_matches) == 1 or (len(amount_matches) > 1 and _MULTI_TARGET_REFERENCE_RE.search(segment)):
            matched_task_ids.extend(amount_matches)

    return list(dict.fromkeys(matched_task_ids))


def _decision_target_texts(decision: Any) -> list[str]:
    return [
        text.strip()
        for text in (str(value or "") for value in getattr(decision, "target_texts", []) or [])
        if text.strip()
    ]


def _task_for_target_id(state: OrchestratorState, task_id: str, *, removed: bool) -> TaskSpec | None:
    if not removed:
        return state.tasks.get(task_id)
    return _removed_task_from_entry((state.removed_confirmation_tasks or {}).get(task_id))


def _scoped_removal_amounts(text: str) -> list[float]:
    amounts: list[float] = []
    for match in _SCOPED_TASK_AMOUNT_RE.finditer(text):
        amount = _parse_scoped_confirmation_amount(match.group(0))
        if amount is None:
            continue
        if amount not in amounts:
            amounts.append(amount)
    return amounts


def _task_ids_matching_amount_reference(
    *,
    reference: str,
    task_ids: list[str],
    state: OrchestratorState,
    removed: bool,
) -> list[str]:
    amounts = _scoped_removal_amounts(reference)
    if not amounts:
        return []

    matched_task_ids: list[str] = []
    for amount in amounts:
        amount_matches = [
            task_id
            for task_id in task_ids
            if (task := _task_for_target_id(state, task_id, removed=removed)) is not None
            and (task_amount := _transfer_task_amount(task)) is not None
            and abs(task_amount - amount) < 0.01
        ]
        matched_task_ids.extend(amount_matches)
    return list(dict.fromkeys(matched_task_ids))


def _removed_task_from_entry(entry: Any) -> TaskSpec | None:
    task = entry.get("task") if isinstance(entry, dict) else None
    if isinstance(task, dict):
        task = TaskSpec.model_validate(task)
    return task if isinstance(task, TaskSpec) else None


def confirmation_scoped_task_removal_ids(
    *,
    state: OrchestratorState,
    interrupt: Any,
    decision: Any,
) -> list[str]:
    if getattr(interrupt, "kind", None) != "confirmation":
        return []

    task_ids = [str(task_id) for task_id in getattr(interrupt, "task_ids", []) if str(task_id) in state.tasks]
    if len(task_ids) < 2:
        return []

    matched_task_ids = _target_task_ids_from_decision(
        decision=decision,
        task_ids=task_ids,
        state=state,
    )
    if not matched_task_ids:
        return []
    if len(matched_task_ids) >= len(task_ids) and not getattr(decision, "target_types", None):
        return []
    return matched_task_ids


def _removed_task_entry(
    *,
    state: OrchestratorState,
    task_id: str,
    task: TaskSpec,
) -> dict[str, Any]:
    wave_index = None
    position = None
    for idx, wave in enumerate(state.waves):
        if task_id in wave:
            wave_index = idx
            position = wave.index(task_id)
            break

    return {
        "task": task.model_copy(deep=True),
        "wave_index": wave_index,
        "position": position,
        "task_result": state.task_results.get(task_id),
    }


def _reset_confirmation_tasks_for_reconfirm(tasks: dict[str, TaskSpec], task_ids: list[str]) -> None:
    reset_tasks_to_extracted(
        tasks,
        task_ids,
        copy_task=False,
        clear_idempotency=True,
    )
    for task_id in task_ids:
        task = tasks.get(task_id)
        if task is None or task.type not in TRANSACTION_INTENTS:
            continue
        task.payload["skip_extraction"] = True
        task.payload.pop("pending_user_message", None)
        task.payload.pop("confirmation_message_scoped", None)


def remove_confirmation_tasks_and_reconfirm_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    task_ids_to_remove: list[str],
) -> dict[str, Any]:
    remove_set = {str(task_id) for task_id in task_ids_to_remove}
    if not remove_set:
        return {}

    removed_tasks = dict(state.removed_confirmation_tasks)
    for task_id in remove_set:
        task = state.tasks.get(task_id)
        if task is None:
            continue
        removed_tasks[task_id] = _removed_task_entry(state=state, task_id=task_id, task=task)

    tasks = {
        task_id: task.model_copy(deep=True)
        for task_id, task in state.tasks.items()
        if task_id not in remove_set
    }
    task_results = {
        task_id: result
        for task_id, result in state.task_results.items()
        if task_id not in remove_set
    }
    waves = [
        [task_id for task_id in wave if task_id not in remove_set]
        for wave in state.waves
    ]
    waves = [wave for wave in waves if wave]
    current_wave_index = min(state.current_wave_index, max(len(waves) - 1, 0))

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
        "last_callback": None,
    }


def confirmation_scoped_task_restore_ids(
    *,
    state: OrchestratorState,
    interrupt: Any,
    decision: Any,
) -> list[str]:
    if getattr(interrupt, "kind", None) != "confirmation":
        return []

    removed = state.removed_confirmation_tasks or {}
    if not removed:
        return []

    removed_task_ids = [str(task_id) for task_id in removed]
    matched_task_ids = _target_task_ids_from_decision(
        decision=decision,
        task_ids=removed_task_ids,
        state=state,
        removed=True,
    )
    if matched_task_ids:
        return matched_task_ids
    if len(removed) == 1 and not getattr(decision, "target_types", None) and not _decision_target_texts(decision):
        return [next(iter(removed.keys()))]
    return []


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

    removed_tasks = dict(state.removed_confirmation_tasks)
    tasks = {task_id: task.model_copy(deep=True) for task_id, task in state.tasks.items()}
    task_results = dict(state.task_results)
    waves = [list(wave) for wave in state.waves]

    restored_task_ids: list[str] = []
    for task_id in restore_set:
        entry = removed_tasks.pop(task_id, None)
        if not isinstance(entry, dict):
            continue
        task = entry.get("task")
        if isinstance(task, dict):
            task = TaskSpec.model_validate(task)
        if not isinstance(task, TaskSpec):
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
        "current_wave_index": min(state.current_wave_index, max(len(waves) - 1, 0)),
        "removed_confirmation_tasks": removed_tasks,
        "pin_verified": False,
        "last_callback": None,
    }
