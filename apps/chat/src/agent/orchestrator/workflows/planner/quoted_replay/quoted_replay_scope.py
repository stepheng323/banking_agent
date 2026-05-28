"""Seed-task scope matching for quoted replay."""

from typing import Any

from shared.types.quoted_replay import QuotedReplayInterpretation

_REPLAY_TASK_TYPES = {"transfer", "airtime", "data"}


def _normalize_replay_status(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"success", "successful", "completed", "confirmed"}:
        return "success"
    if normalized in {"processing", "pending", "queued"}:
        return "processing"
    if normalized in {"failed", "error"}:
        return "failed"
    return None


def _quoted_seed_task_items(quoted_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(quoted_payload, dict) or not quoted_payload:
        return []
    if isinstance(quoted_payload.get("tasks"), list):
        candidates = [task for task in quoted_payload["tasks"] if isinstance(task, dict)]
    else:
        candidates = [quoted_payload]
    seed_tasks: list[dict[str, Any]] = []
    for candidate in candidates:
        task_type = str(candidate.get("task_type") or "").strip().lower()
        if task_type not in _REPLAY_TASK_TYPES:
            continue
        seed_tasks.append(candidate)
    return seed_tasks


def _scope_requested(interpretation: QuotedReplayInterpretation) -> bool:
    return bool(interpretation.target_statuses or interpretation.target_types or interpretation.target_task_ids)


def _seed_task_matches_scope(task: dict[str, Any], interpretation: QuotedReplayInterpretation) -> bool:
    target_task_ids = {str(task_id).strip() for task_id in interpretation.target_task_ids if str(task_id).strip()}
    target_types = {str(task_type).strip().lower() for task_type in interpretation.target_types}
    target_statuses = {str(status).strip().lower() for status in interpretation.target_statuses}

    if target_task_ids:
        task_ids = {
            str(task.get("task_id") or "").strip(),
            str(task.get("id") or "").strip(),
            str(task.get("transaction_id") or "").strip(),
            str(task.get("idempotency_key") or "").strip(),
        }
        if not task_ids.intersection(target_task_ids):
            return False

    if target_types and str(task.get("task_type") or "").strip().lower() not in target_types:
        return False

    if target_statuses:
        normalized_status = _normalize_replay_status(task.get("final_status")) or "success"
        if normalized_status not in target_statuses:
            return False

    return True


def _select_seed_tasks(
    *,
    quoted_payload: dict[str, Any] | None,
    interpretation: QuotedReplayInterpretation,
) -> list[dict[str, Any]]:
    seed_tasks = _quoted_seed_task_items(quoted_payload)
    if not seed_tasks:
        return []
    if not _scope_requested(interpretation):
        return seed_tasks
    return [task for task in seed_tasks if _seed_task_matches_scope(task, interpretation)]


__all__ = [
    "_scope_requested",
    "_select_seed_tasks",
]
