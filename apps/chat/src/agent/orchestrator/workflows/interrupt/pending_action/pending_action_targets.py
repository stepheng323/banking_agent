"""Target resolution helpers for pending-action edits."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_scope import (
    _transfer_task_reference_matches,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_target_matching import (
    _message_targets_confirmation_task,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import TRANSACTION_INTENTS


def _pending_edit_target_task_ids(
    *,
    state: OrchestratorState,
    interrupt: Any,
    decision: Any,
    field: str,
) -> list[str]:
    active_task_ids = [str(task_id) for task_id in getattr(interrupt, "task_ids", []) if str(task_id) in state.tasks]
    if not active_task_ids:
        return []

    explicit_ids = [task_id for task_id in decision.target_task_ids if task_id in active_task_ids]
    if explicit_ids:
        return list(dict.fromkeys(explicit_ids))

    matched_ids: list[str] = []
    for reference in getattr(decision, "target_texts", []) or []:
        target_text = str(reference or "").strip()
        if not target_text:
            continue
        reference_matches = [
            task_id
            for task_id in active_task_ids
            if (task := state.tasks.get(task_id)) is not None
            and (
                _message_targets_confirmation_task(target_text, task)
                or (task.type == "transfer" and _transfer_task_reference_matches(target_text, task))
            )
        ]
        if len(reference_matches) == 1:
            matched_ids.extend(reference_matches)

    if matched_ids:
        return list(dict.fromkeys(matched_ids))

    target_types = {str(task_type) for task_type in getattr(decision, "target_types", []) if task_type}
    if target_types:
        type_matches = [
            task_id
            for task_id in active_task_ids
            if (task := state.tasks.get(task_id)) is not None and task.type in target_types
        ]
        if _field_can_apply_collectively(field):
            return [
                task_id
                for task_id in type_matches
                if (task := state.tasks.get(task_id)) is not None
                and _field_applies_to_task(field, task.type)
            ]
        if field in {"source_bank_name", "source_account_index"}:
            return type_matches
        if len(type_matches) == 1:
            return type_matches

    compatible_task_ids = [
        task_id
        for task_id in active_task_ids
        if (task := state.tasks.get(task_id)) is not None
        and _field_applies_to_task(field, task.type)
    ]
    if field in {"source_bank_name", "source_account_index"}:
        return compatible_task_ids
    if len(compatible_task_ids) == 1:
        return compatible_task_ids

    return []


_DATA_PLAN_EDIT_FIELDS = {
    "size_preference",
    "validity_preference",
    "selection_preference",
    "usage_intent",
    "show_options",
}


def _field_can_apply_collectively(field: str) -> bool:
    return field in {
        "amount",
        "narration",
        "phone",
        "network",
        "source_accounts",
        "use_dual_accounts",
        *_DATA_PLAN_EDIT_FIELDS,
    }


def _field_applies_to_task(field: str, task_type: str) -> bool:
    if field in {"amount", "source_bank_name", "source_account_index"}:
        return task_type in TRANSACTION_INTENTS
    if field in {
        "narration",
        "recipient_name",
        "recipient_account",
        "recipient_bank_name",
        "source_accounts",
        "use_dual_accounts",
        "funding_splits",
    }:
        return task_type == "transfer"
    if field in {"phone", "network"}:
        return task_type in {"airtime", "data"}
    if field in _DATA_PLAN_EDIT_FIELDS:
        return task_type == "data"
    return False

__all__ = [
    "TRANSACTION_INTENTS",
    "_DATA_PLAN_EDIT_FIELDS",
    "_field_applies_to_task",
    "_field_can_apply_collectively",
    "_pending_edit_target_task_ids",
]
