"""Amount-reference matching for scoped confirmation edits."""

import re

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_edit_target_tasks import (
    _task_for_target_id,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_scope import (
    _parse_scoped_confirmation_amount,
    _transfer_task_amount,
)

_SCOPED_TASK_AMOUNT_RE = re.compile(
    r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?",
    re.IGNORECASE,
)


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


__all__ = ["_scoped_removal_amounts", "_task_ids_matching_amount_reference"]
