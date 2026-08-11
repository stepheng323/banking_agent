"""Privacy-safe state snapshots for adversarial readiness evaluation.

The evaluator must be able to compare turns without serializing checkpoints,
account identifiers, transaction references or user text.  This module keeps
the snapshot vocabulary deliberately small and stable so scenario fixtures can
assert semantic state rather than implementation details.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

_PAYLOAD_PRESENCE_FIELDS = (
    "amount",
    "recipient_name",
    "recipient_bank_name",
    "recipient_account",
    "source_account_id",
    "source_account_number",
    "source_bank_name",
    "is_self",
    "required_fields",
)


def _stage_value(value: Any) -> str | None:
    raw = getattr(value, "value", value)
    return str(raw) if isinstance(raw, str) and raw else None


def _task_signature(task: Any) -> dict[str, Any]:
    payload = getattr(task, "payload", None)
    payload = payload if isinstance(payload, dict) else {}
    confirmation = payload.get("confirmation")
    confirmation = confirmation if isinstance(confirmation, dict) else {}
    required_fields = payload.get("required_fields")
    if isinstance(required_fields, (list, tuple, set)):
        required = tuple(sorted(str(field) for field in required_fields if str(field).strip()))
    else:
        required = ()
    return {
        "type": str(getattr(task, "type", "") or ""),
        "stage": _stage_value(getattr(task, "stage", None)),
        "action": str(payload.get("action") or "") or None,
        "present_fields": tuple(field for field in _PAYLOAD_PRESENCE_FIELDS if field in payload),
        "required_fields": required,
        "is_self": payload.get("is_self") is True,
        "confirmation_confirmed": confirmation.get("confirmed") is True,
    }


def _directive_snapshot(state: Any) -> dict[str, Any] | None:
    directive = getattr(state, "turn_directive", None)
    if directive is None:
        return None
    if hasattr(directive, "model_dump"):
        raw = directive.model_dump(mode="json")
    elif isinstance(directive, dict):
        raw = directive
    else:
        return None
    return {
        field: raw.get(field)
        for field in (
            "path_shape",
            "owner",
            "decision",
            "outcome_kind",
            "next_step",
            "target_domain",
            "mode",
            "source",
        )
        if raw.get(field) is not None
    }


def snapshot_state(state: Any) -> dict[str, Any]:
    """Return a bounded, identifier-free semantic snapshot of orchestrator state."""

    raw_tasks = getattr(state, "tasks", None)
    tasks = list(raw_tasks.values()) if isinstance(raw_tasks, dict) else []
    signatures = sorted(
        (_task_signature(task) for task in tasks),
        key=lambda item: (
            str(item.get("type") or ""),
            str(item.get("action") or ""),
            str(item.get("stage") or ""),
            bool(item.get("is_self")),
        ),
    )
    task_types = tuple(signature["type"] for signature in signatures if signature["type"])
    stage_counts = Counter(str(signature["stage"]) for signature in signatures if signature.get("stage"))

    pending = getattr(state, "pending_interrupt", None)
    pending_kind = getattr(pending, "kind", None)
    pending_task_ids = getattr(pending, "task_ids", None)
    pending_fields = getattr(pending, "fields_by_task", None)
    pending_task_count = len(pending_task_ids) if isinstance(pending_task_ids, list) else 0
    pending_field_count = 0
    if isinstance(pending_fields, dict):
        pending_field_count = sum(
            len(fields) for fields in pending_fields.values() if isinstance(fields, (list, tuple, set))
        )

    pending_query = getattr(state, "pending_query_clarification", None)
    query_frames = getattr(state, "query_frames", None)
    if not isinstance(query_frames, list):
        query_frames = []
    stashed_sessions = getattr(state, "stashed_sessions", None)
    if not isinstance(stashed_sessions, list):
        stashed_sessions = []

    return {
        "available": True,
        "tasks": {
            "count": len(signatures),
            "types": task_types,
            "stage_counts": dict(sorted(stage_counts.items())),
            "signatures": signatures,
        },
        "pending_interrupt": {
            "kind": pending_kind if isinstance(pending_kind, str) else None,
            "task_count": pending_task_count,
            "field_count": pending_field_count,
        },
        "active_domain": getattr(state, "active_domain", None),
        "stashed_sessions": {
            "count": len(stashed_sessions),
        },
        "query": {
            "frames_count": len(query_frames),
            "has_pending_clarification": bool(pending_query),
            "session_active": bool(getattr(state, "query_session_active", False)),
        },
        "turn_directive": _directive_snapshot(state),
    }


__all__ = ["snapshot_state"]
