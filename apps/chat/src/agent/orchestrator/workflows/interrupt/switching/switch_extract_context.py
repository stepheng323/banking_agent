"""Extractor context builders for interrupt switch turns."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _state_locale


def _interrupt_required_fields(interrupt: Any) -> list[str]:
    fields_by_task = getattr(interrupt, "fields_by_task", {}) or {}
    if not isinstance(fields_by_task, dict):
        return []
    ordered: list[str] = []
    seen: set[str] = set()
    for raw_fields in fields_by_task.values():
        if not isinstance(raw_fields, list):
            continue
        for field in raw_fields:
            if not isinstance(field, str) or field in seen:
                continue
            ordered.append(field)
            seen.add(field)
    return ordered


def _build_transaction_extractor_context(
    *,
    state: OrchestratorState,
    interrupt: Any,
    target_intent: str,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "phone_number": state.phone_number,
        "language": _state_locale(state),
        "accounts": (state.loaded_context or {}).get("accounts", []),
        "beneficiaries": (state.loaded_context or {}).get("beneficiaries", []),
        "required_fields": _interrupt_required_fields(interrupt),
        "previousResponse": getattr(interrupt, "prompt", None),
        "previous_response": getattr(interrupt, "prompt", None),
    }

    if target_intent != "transfer":
        return context

    first_task_id = next(iter(getattr(interrupt, "task_ids", []) or []), None)
    task = state.tasks.get(str(first_task_id)) if first_task_id else None
    payload = task.payload if task and isinstance(task.payload, dict) else {}
    context["known_recipient"] = {
        "recipient_name": payload.get("recipient_name"),
        "recipient_resolved_name": payload.get("recipient_resolved_name"),
        "recipient_account": payload.get("recipient_account"),
        "recipient_bank_name": payload.get("recipient_bank_name"),
    }
    return context


__all__ = [
    "_build_transaction_extractor_context",
    "_interrupt_required_fields",
]
