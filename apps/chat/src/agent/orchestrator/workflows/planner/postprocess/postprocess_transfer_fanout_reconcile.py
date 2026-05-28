"""Recipient reconciliation for multi-transfer planner output."""

from typing import Any

from apps.chat.src.agent.orchestrator.utils.task_payload_recipients import derive_recipients_from_user_text
from apps.chat.src.agent.orchestrator.workflows.planner.postprocess.postprocess_transfer_fanout_common import (
    _normalize_recipient_text,
    _recipient_overlap_score,
)
from shared.types.planner import PlannedTask


def _reconcile_multi_transfer_recipient_tasks(
    planned_tasks: list[PlannedTask],
    user_text: str,
) -> tuple[list[PlannedTask], dict[str, Any] | None]:
    """Repair obvious recipient drift when planner already emitted multiple transfer tasks.

    This is intentionally conservative. It only applies when:
    - user text clearly contains an ordered multi-recipient list
    - planner already emitted the same number of transfer tasks
    - each task can be matched unambiguously to one recipient
    """

    recipients = derive_recipients_from_user_text(user_text)
    if len(recipients) < 2:
        return planned_tasks, None

    transfer_indices = [idx for idx, task in enumerate(planned_tasks) if task.executor == "transfer"]
    if len(transfer_indices) < 2 or len(transfer_indices) != len(recipients):
        return planned_tasks, None

    transfer_tasks = [planned_tasks[idx] for idx in transfer_indices]
    if any(task.parameters.recipient_allocations for task in transfer_tasks):
        return planned_tasks, None
    if any(task.parameters.recipient_account or task.parameters.bank_name for task in transfer_tasks):
        return planned_tasks, None

    recipient_lookup = {_normalize_recipient_text(recipient): recipient for recipient in recipients}
    if len(recipient_lookup) != len(recipients):
        return planned_tasks, None

    assignments: dict[int, str] = {}
    used_recipient_keys: set[str] = set()

    for idx in transfer_indices:
        task = planned_tasks[idx]
        current_name = str(task.parameters.recipient_name or task.parameters.recipient or "").strip()
        current_key = _normalize_recipient_text(current_name)
        if current_key and current_key in recipient_lookup and current_key not in used_recipient_keys:
            assignments[idx] = current_name
            used_recipient_keys.add(current_key)

    for idx in transfer_indices:
        if idx in assignments:
            continue

        task = planned_tasks[idx]
        current_name = str(task.parameters.recipient_name or task.parameters.recipient or "").strip()
        available = [
            (recipient_key, recipient_lookup[recipient_key])
            for recipient_key in recipient_lookup
            if recipient_key not in used_recipient_keys
        ]
        if not available:
            return planned_tasks, None

        scored = [
            (recipient_key, recipient_name, _recipient_overlap_score(current_name, recipient_name))
            for recipient_key, recipient_name in available
        ]
        scored.sort(key=lambda item: item[2], reverse=True)
        best_key, best_name, best_score = scored[0]
        if best_score <= 0:
            return planned_tasks, None
        if len(scored) > 1 and scored[1][2] == best_score:
            return planned_tasks, None

        assignments[idx] = best_name
        used_recipient_keys.add(best_key)

    changed_tasks: list[tuple[str, str, str]] = []
    normalized_tasks = [task.model_copy(deep=True) for task in planned_tasks]
    for idx in transfer_indices:
        task = normalized_tasks[idx]
        previous_name = str(task.parameters.recipient_name or task.parameters.recipient or "").strip()
        next_name = assignments.get(idx)
        if not next_name or previous_name == next_name:
            continue
        task.parameters.recipient = next_name
        task.parameters.recipient_name = next_name
        changed_tasks.append((task.task_id, previous_name, next_name))

    if not changed_tasks:
        return planned_tasks, None

    return (
        normalized_tasks,
        {
            "recipient_count": len(recipients),
            "recipient_names": recipients,
            "changed_tasks": changed_tasks,
            "repair_mode": "multi_transfer_recipient_reconcile",
        },
    )


__all__ = [
    "_reconcile_multi_transfer_recipient_tasks",
]
