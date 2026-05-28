"""Beneficiary-specific helpers for input interrupts."""

from typing import Any


def _is_beneficiary_clarification_interrupt(interrupt: Any) -> bool:
    if not interrupt or getattr(interrupt, "kind", None) != "input":
        return False
    fields_by_task = getattr(interrupt, "fields_by_task", {}) or {}
    if not isinstance(fields_by_task, dict):
        return False
    return any(isinstance(fields, list) and "beneficiary_id" in fields for fields in fields_by_task.values())


__all__ = ["_is_beneficiary_clarification_interrupt"]
