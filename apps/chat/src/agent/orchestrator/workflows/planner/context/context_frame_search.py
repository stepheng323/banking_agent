"""Text and amount search for context-frame entities."""

from decimal import Decimal

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_ranking import numeric_rank_value
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_text import (
    amount_reference_values,
    lookup_tokens,
    normalize,
    semantic_tokens,
    token_matches_searchable,
)
from shared.money import MoneyAmount, to_naira

SEARCHABLE_DATA_KEYS = (
    "alias",
    "name",
    "account_name",
    "bank_name",
    "bank",
    "category",
    "merchant",
    "network",
    "group_by",
    "group_key",
    "account_number",
    "account",
    "counterparty",
    "description",
    "direction",
    "recipient_name",
    "recipient_resolved_name",
    "target",
    "reference",
    "schedule_id",
    "schedule_time",
    "next_run",
    "recurrence",
    "status",
    "mandate_status",
    "is_default",
    "transaction_type",
    "type",
    "plan_name",
    "item_code",
    "plan_code",
    "size_gb",
    "validity_days",
)


def _entity_matches_amount_reference(entity: ContextEntity, amount_refs: set[MoneyAmount]) -> bool:
    if not amount_refs:
        return False
    value = numeric_rank_value(entity)
    if value is None:
        return False
    amount = to_naira(value)
    if amount is None:
        return False
    abs_value = abs(amount)
    return any(abs(abs_value - abs(target)) < Decimal("0.01") for target in amount_refs)


def _searchable_text(entity: ContextEntity) -> str:
    parts = [entity.label or ""]
    data = entity.data if isinstance(entity.data, dict) else {}
    for key in SEARCHABLE_DATA_KEYS:
        value = data.get(key)
        if value is not None:
            parts.append(str(value))
    return normalize(" ".join(parts))


def find_matching_entities(frame: ContextFrame, lookup_query: str) -> list[ContextEntity]:
    query_tokens = lookup_tokens(lookup_query)
    amount_refs = amount_reference_values(lookup_query)
    if not query_tokens and not amount_refs:
        return []

    amount_matches = [entity for entity in frame.items if _entity_matches_amount_reference(entity, amount_refs)]
    if amount_matches:
        return amount_matches

    matches: list[ContextEntity] = []
    for entity in frame.items:
        searchable = _searchable_text(entity)
        if all(token_matches_searchable(token, searchable) for token in query_tokens):
            matches.append(entity)
    if matches:
        return matches

    # Fall back to any-token match so short references like "tolu" can bind to a list
    # where the visible labels carry branch qualifiers.
    for entity in frame.items:
        searchable = _searchable_text(entity)
        if any(token_matches_searchable(token, searchable) for token in query_tokens):
            matches.append(entity)
    return matches


def account_status_grounded_entities(frame: ContextFrame, text: str) -> list[ContextEntity]:
    """Find account entities when text names the account and one of its visible statuses."""
    if frame.frame_type != ContextFrameType.ACCOUNT_LIST:
        return []

    tokens = set(lookup_tokens(text)) | semantic_tokens(text)
    if not tokens:
        return []

    matches: list[ContextEntity] = []
    for entity in frame.items:
        data = entity.data if isinstance(entity.data, dict) else {}
        status = str(data.get("mandate_status") or data.get("status") or "").strip().lower()
        if not status or not any(token_matches_searchable(token, status) for token in tokens):
            continue

        identity_values = [
            entity.label or "",
            str(data.get("bank_name") or ""),
            str(data.get("bank") or ""),
            str(data.get("account_number") or ""),
        ]
        identity = normalize(" ".join(identity_values))
        if any(token_matches_searchable(token, identity) for token in tokens):
            matches.append(entity)
    return matches


__all__ = [
    "SEARCHABLE_DATA_KEYS",
    "account_status_grounded_entities",
    "find_matching_entities",
]
