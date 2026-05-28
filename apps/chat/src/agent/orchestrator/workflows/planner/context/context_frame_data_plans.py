"""Data-plan helpers for context-frame follow-up responses."""

import re
from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame
from shared.formatters.currency import format_naira_compact


def is_data_plan_entity(entity: ContextEntity) -> bool:
    return entity.entity_type.value == "data_plan"


def is_data_plan_frame(frame: ContextFrame) -> bool:
    return bool(frame.items) and all(is_data_plan_entity(entity) for entity in frame.items)


def _data_plan_display_key(entity: ContextEntity) -> tuple[str, str, float | None, int | None]:
    data = entity.data if isinstance(entity.data, dict) else {}
    amount: float | None
    try:
        amount = float(data.get("amount")) if data.get("amount") is not None else None
    except (TypeError, ValueError):
        amount = None
    validity: int | None
    try:
        validity = int(float(data.get("validity_days"))) if data.get("validity_days") is not None else None
    except (TypeError, ValueError):
        validity = None
    name = str(data.get("plan_name") or data.get("name") or entity.label or "").strip().casefold()
    normalized_name = re.sub(r"\b(\d+(?:\.\d+)?)\s*(?:gb|g)\b", r"\1gb", name)
    normalized_name = re.sub(r"\b(\d+(?:\.\d+)?)\s*(?:mb|m)\b", r"\1mb", normalized_name)
    normalized_name = re.sub(r"\s+", " ", normalized_name).strip()
    network = str(data.get("network") or "").strip().upper()
    return network, normalized_name, amount, validity


def unique_data_plan_entities(entities: list[ContextEntity]) -> list[ContextEntity]:
    unique: list[ContextEntity] = []
    seen: set[tuple[str, str, float | None, int | None]] = set()
    for entity in entities:
        if not is_data_plan_entity(entity):
            unique.append(entity)
            continue
        key = _data_plan_display_key(entity)
        if key in seen:
            continue
        seen.add(key)
        unique.append(entity)
    return unique


def _format_data_plan_validity(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        days = int(float(value))
    except (TypeError, ValueError):
        return None
    if days <= 0:
        return None
    return f"{days} day" if days == 1 else f"{days} days"


def format_data_plan_detail_block(entity: ContextEntity, *, ordinal: int | None = None) -> str | None:
    data = entity.data if isinstance(entity.data, dict) else {}
    name = str(data.get("plan_name") or data.get("name") or entity.label or "Data plan").strip()
    amount = data.get("amount")
    amount_text = format_naira_compact(amount) if amount is not None else None
    validity_text = _format_data_plan_validity(data.get("validity_days"))
    if amount_text and validity_text:
        line = f"{name} is {amount_text}, valid for {validity_text}."
    elif amount_text:
        line = f"{name} is {amount_text}."
    elif validity_text:
        line = f"{name} is valid for {validity_text}."
    else:
        line = name
    if ordinal is not None:
        return f"{ordinal}. {line}"
    return line


__all__ = [
    "format_data_plan_detail_block",
    "is_data_plan_entity",
    "is_data_plan_frame",
    "unique_data_plan_entities",
]
