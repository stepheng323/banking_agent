"""Source-account propagation helpers for execution prompts."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.common import TERMINAL_STAGES, TRANSACTION_TASK_TYPES
from shared.formatters.confirmation import strip_source_account_info_lines
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _is_source_selection_reply(state: OrchestratorState, task_id: str) -> bool:
    interrupt = state.last_interrupt
    if not interrupt or interrupt.kind != "input" or task_id not in interrupt.task_ids:
        return False
    required_fields = set(interrupt.fields_by_task.get(task_id) or [])
    return required_fields == {"source_account_id"}


def _is_same_batch_source_selection_sibling(state: OrchestratorState, task_id: str) -> bool:
    interrupt = state.last_interrupt
    if not interrupt or interrupt.kind != "input":
        return False

    task = state.tasks.get(task_id)
    if not task or task.type not in TRANSACTION_TASK_TYPES:
        return False

    group_id = task.payload.get("async_group_id")
    if not group_id:
        return False

    for active_task_id in interrupt.task_ids:
        required_fields = set(interrupt.fields_by_task.get(active_task_id) or [])
        if required_fields != {"source_account_id"}:
            continue
        active_task = state.tasks.get(active_task_id)
        if active_task and active_task.payload.get("async_group_id") == group_id:
            return True
    return False


def _source_patch_from_task(task_payload: dict[str, Any]) -> dict[str, Any] | None:
    source_account_id = task_payload.get("source_account_id")
    if not source_account_id:
        return None
    patch = {
        "source_account_id": source_account_id,
        "source_bank_name": task_payload.get("source_bank_name"),
        "source_account_name": task_payload.get("source_account_name"),
        "source_account_number": task_payload.get("source_account_number"),
        "source_affinity_mode": "explicit",
        "source_account_index": None,
        "funding_plan": None,
    }
    return {key: value for key, value in patch.items() if value is not None}


def _apply_source_patch_to_confirmation(
    task_payload: dict[str, Any],
    source_patch: dict[str, Any],
    *,
    locale: str,
) -> None:
    confirmation = task_payload.get("confirmation")
    if not isinstance(confirmation, dict):
        return

    snapshot = confirmation.get("snapshot")
    if isinstance(snapshot, dict):
        if source_patch.get("source_bank_name"):
            snapshot["sourceBank"] = source_patch["source_bank_name"]
        if source_patch.get("source_account_number"):
            snapshot["sourceAccount"] = source_patch["source_account_number"]

    summary = confirmation.get("summary")
    if isinstance(summary, str):
        confirmation["summary"] = strip_source_account_info_lines(summary, locale=locale)
    confirmation["confirmed"] = False


def _propagate_batch_source_selection(
    *,
    state: OrchestratorState,
    selected_task_id: str,
    locale: str,
) -> list[str]:
    if not _is_source_selection_reply(state, selected_task_id):
        return []

    selected_task = state.tasks.get(selected_task_id)
    if not selected_task or selected_task.type not in TRANSACTION_TASK_TYPES:
        return []

    group_id = selected_task.payload.get("async_group_id")
    if not group_id:
        return []

    source_patch = _source_patch_from_task(selected_task.payload)
    if not source_patch:
        return []

    propagated_task_ids: list[str] = []
    for task_id, task in state.tasks.items():
        if task_id == selected_task_id:
            continue
        if task.type not in TRANSACTION_TASK_TYPES or task.stage in TERMINAL_STAGES:
            continue
        if task.payload.get("async_group_id") != group_id:
            continue
        if task.payload.get("source_affinity_mode") == "explicit" and task.payload.get("source_account_id"):
            continue

        task.payload.update(source_patch)
        _apply_source_patch_to_confirmation(task.payload, source_patch, locale=locale)
        propagated_task_ids.append(task_id)
        logger.info(
            "batch_source_selection_propagated",
            selected_task_id=selected_task_id,
            task_id=task_id,
            async_group_id=group_id,
            source_account_id=source_patch.get("source_account_id"),
        )
    return propagated_task_ids


__all__ = [
    "_apply_source_patch_to_confirmation",
    "_is_same_batch_source_selection_sibling",
    "_is_source_selection_reply",
    "_propagate_batch_source_selection",
    "_source_patch_from_task",
]
