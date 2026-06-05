"""Field normalization helpers for semantic pending-action edits."""

from typing import Any


def _pending_action_fields(raw_fields: Any) -> dict[str, Any]:
    fields = (
        raw_fields.model_dump(exclude_none=True)
        if hasattr(raw_fields, "model_dump")
        else dict(raw_fields or {})
        if isinstance(raw_fields, dict)
        else {}
    )
    return fields


def _pending_edit_has_fields(decision: Any) -> bool:
    fields = _pending_action_fields(getattr(decision, "fields", None))
    if any(value not in (None, "") for value in fields.values()):
        return True
    for scoped_update in getattr(decision, "updates", []) or []:
        scoped_fields = _pending_action_fields(getattr(scoped_update, "fields", None))
        if any(value not in (None, "") for value in scoped_fields.values()):
            return True
    return False


__all__ = [
    "_pending_action_fields",
    "_pending_edit_has_fields",
]
