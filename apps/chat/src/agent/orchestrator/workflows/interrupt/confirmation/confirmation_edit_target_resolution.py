"""Decision-to-task resolution for scoped confirmation edits."""

import re
from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_edit_target_amounts import (
    _task_ids_matching_amount_reference,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_edit_target_tasks import (
    _task_for_target_id,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_scope import (
    _task_is_self_transfer,
    _transfer_task_reference_matches,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_target_matching import (
    _message_targets_confirmation_task,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import TRANSACTION_INTENTS

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
        str(task_id) for task_id in (getattr(decision, "target_task_ids", []) or []) if str(task_id) in task_ids
    ]
    if explicit_ids:
        return list(dict.fromkeys(explicit_ids))

    target_texts = _decision_target_texts(decision)
    target_types = {
        str(task_type).strip().lower()
        for task_type in (getattr(decision, "target_types", []) or [])
        if str(task_type).strip().lower() in TRANSACTION_INTENTS
    }
    if target_types:
        # A broad LLM type hint must not override a narrower typed reference.
        # In particular, "remove the self transfer" can arrive with
        # target_types=["transfer"] plus target_texts=["self transfer"].
        # Resolve the self leg first rather than removing every transfer leg.
        self_reference_matches = [
            task_id
            for task_id in task_ids
            if (task := _task_for_target_id(state, task_id, removed=removed)) is not None
            and task.type == "transfer"
            and _task_is_self_transfer(task)
            and any(_transfer_task_reference_matches(segment, task) for segment in target_texts)
        ]
        if self_reference_matches:
            return list(dict.fromkeys(self_reference_matches))
        return [
            task_id
            for task_id in task_ids
            if (task := _task_for_target_id(state, task_id, removed=removed)) is not None and task.type in target_types
        ]

    matched_task_ids: list[str] = []
    for segment in target_texts:
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


__all__ = ["_decision_target_texts", "_target_task_ids_from_decision"]
