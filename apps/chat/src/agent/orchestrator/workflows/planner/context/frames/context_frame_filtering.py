"""Typed filter matching for context-frame follow-up responses."""

from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_text import (
    lookup_tokens,
    normalize,
    semantic_tokens,
    token_matches_searchable,
)
from shared.types.planner import ContextFrameFollowupFilters

_FILTER_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "transaction_type": ("task_type", "transaction_type", "type", "direction"),
    "status": ("status", "provider_status", "final_status", "mandate_status"),
    "direction": ("direction", "transaction_type", "type"),
    "bank": ("bank_name", "bank", "recipient_bank_name", "source_bank_name"),
    "counterparty": ("counterparty", "recipient_resolved_name", "recipient_name", "merchant", "name", "label"),
}


def has_filters(filters: ContextFrameFollowupFilters | None) -> bool:
    if filters is None:
        return False
    return any(
        bool(value)
        for value in (
            filters.transaction_type,
            filters.status,
            filters.direction,
            filters.bank,
            filters.counterparty,
        )
    )


def _value_matches_filter(value: Any, expected: str) -> bool:
    if value is None or value == "":
        return False
    expected_tokens = lookup_tokens(expected) or list(semantic_tokens(expected))
    if not expected_tokens:
        return False
    haystack = normalize(str(value))
    return all(token_matches_searchable(token, haystack) for token in expected_tokens)


def _entity_matches_filter(entity: ContextEntity, filter_name: str, expected: str) -> bool:
    data = entity.data if isinstance(entity.data, dict) else {}
    for key in _FILTER_FIELD_ALIASES[filter_name]:
        value = entity.label if key == "label" else data.get(key)
        if _value_matches_filter(value, expected):
            return True
    return False


def find_filtered_entities(
    frame: ContextFrame,
    filters: ContextFrameFollowupFilters | None,
) -> list[ContextEntity]:
    if not has_filters(filters) or filters is None:
        return []

    active_filters = {
        "transaction_type": filters.transaction_type,
        "status": filters.status,
        "direction": filters.direction,
        "bank": filters.bank,
        "counterparty": filters.counterparty,
    }
    matches: list[ContextEntity] = []
    for entity in frame.items:
        if all(
            _entity_matches_filter(entity, filter_name, expected)
            for filter_name, expected in active_filters.items()
            if expected
        ):
            matches.append(entity)
    return matches


__all__ = ["find_filtered_entities", "has_filters"]
