"""Context-frame replay payload value extraction."""

from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextEntity


def transaction_task_type(entity: ContextEntity) -> str:
    data = entity.data if isinstance(entity.data, dict) else {}
    payload_type = ""
    if entity.selection_payload is not None:
        payload_type = str(entity.selection_payload.entity_type or "")
    raw_type = data.get("task_type") or data.get("transaction_type") or data.get("type") or payload_type
    return str(raw_type).strip().lower()


def payload_value(entity: ContextEntity, *keys: str) -> Any:
    data = entity.data if isinstance(entity.data, dict) else {}
    handoff = entity.selection_payload.handoff_payload if entity.selection_payload is not None else None
    sources = [handoff if isinstance(handoff, dict) else {}, data]
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value is not None and value != "":
                return value
    return None


__all__ = ["payload_value", "transaction_task_type"]
