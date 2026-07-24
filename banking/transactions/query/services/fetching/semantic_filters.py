"""Deterministic filtering for Query Semantics v2 scopes."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from banking.transactions.query.models.operations import (
    AmountRange,
    ApproximateAmount,
    ContextualCounterparty,
    ExactAmount,
    NamedCounterparty,
    QueryScope,
    SavedBeneficiaryCounterparty,
    UnspecifiedCounterparty,
)
from banking.transactions.query.services.fetching.fetch import (
    apply_time_window,
    normalize_transaction_status,
)

_PAYMENT_NARRATION_TOKENS = ("transfer", "payment", "paid", "sent", "nip", "beneficiary")


def _normalized_text(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _amount(value: object) -> Decimal | None:
    try:
        return abs(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _matches_amount(transaction: dict[str, Any], constraint: object) -> bool:
    value = _amount(transaction.get("amount"))
    if value is None:
        return False
    if isinstance(constraint, ExactAmount):
        return value == constraint.value.amount
    if isinstance(constraint, ApproximateAmount):
        return constraint.minimum <= value <= constraint.maximum
    if isinstance(constraint, AmountRange):
        if constraint.minimum is not None:
            if constraint.minimum_inclusive and value < constraint.minimum.amount:
                return False
            if not constraint.minimum_inclusive and value <= constraint.minimum.amount:
                return False
        if constraint.maximum is not None:
            if constraint.maximum_inclusive and value > constraint.maximum.amount:
                return False
            if not constraint.maximum_inclusive and value >= constraint.maximum.amount:
                return False
        return True
    return True


def _counterparty_text(transaction: dict[str, Any]) -> str:
    return _normalized_text(
        transaction.get("counterparty")
        or transaction.get("recipient_name")
        or transaction.get("sender_name")
        or transaction.get("merchant")
    )


def _role_matches(transaction: dict[str, Any], role: str) -> bool:
    if role == "any":
        return True
    direction = _normalized_text(transaction.get("type") or transaction.get("transaction_type"))
    if role == "sender":
        return direction == "credit"
    if role == "recipient":
        return direction == "debit"
    if role == "merchant":
        transaction_role = _normalized_text(transaction.get("counterparty_role"))
        return transaction_role in {"merchant", "payee"} or bool(transaction.get("merchant"))
    return False


def _bounded_contains(haystack: str, needle: str) -> bool:
    if not needle:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


def _matches_counterparty(transaction: dict[str, Any], selector: object) -> bool:
    from banking.transactions.query.models.operations import CounterpartySelector

    if not isinstance(selector, CounterpartySelector) or not _role_matches(transaction, selector.role):
        return False
    reference = selector.reference
    counterparty = _counterparty_text(transaction)
    narration = _normalized_text(transaction.get("narration"))

    if isinstance(reference, UnspecifiedCounterparty):
        if not counterparty:
            return False
        if reference.entity_type == "merchant":
            return _role_matches(transaction, "merchant")
        return True
    if isinstance(reference, NamedCounterparty):
        name = _normalized_text(reference.name)
        return counterparty == name or _bounded_contains(counterparty, name) or _bounded_contains(narration, name)
    if isinstance(reference, ContextualCounterparty):
        if reference.entity_id is None:
            return False
        return reference.entity_id in {
            str(transaction.get("counterparty_id") or ""),
            str(transaction.get("recipient_id") or ""),
            str(transaction.get("sender_id") or ""),
        }
    if isinstance(reference, SavedBeneficiaryCounterparty):
        if reference.beneficiary_id and reference.beneficiary_id == str(transaction.get("beneficiary_id") or ""):
            return True
        return bool(reference.name and counterparty == _normalized_text(reference.name))
    return False


def _matches_event_types(transaction: dict[str, Any], event_types: list[str]) -> bool:
    if not event_types:
        return True
    event_type = _normalized_text(transaction.get("event_type"))
    if event_type:
        return event_type in {_normalized_text(item) for item in event_types}
    narration = _normalized_text(transaction.get("narration"))
    return any(token in narration for token in _PAYMENT_NARRATION_TOKENS)


def apply_query_scope(transactions: list[dict[str, Any]], scope: QueryScope) -> list[dict[str, Any]]:
    """Apply a validated v2 scope using deterministic AND semantics."""

    result = apply_time_window(
        transactions,
        window_start=scope.period.start,
        window_end=scope.period.end,
    )
    predicate = scope.predicate
    if predicate.amount is not None:
        result = [item for item in result if _matches_amount(item, predicate.amount)]
    if predicate.direction is not None:
        result = [
            item
            for item in result
            if _normalized_text(item.get("type") or item.get("transaction_type")) == predicate.direction
        ]
    if predicate.statuses:
        statuses = set(predicate.statuses)
        result = [item for item in result if normalize_transaction_status(item) in statuses]
    if predicate.categories:
        categories = {_normalized_text(item) for item in predicate.categories}
        result = [
            item
            for item in result
            if _normalized_text(item.get("resolved_category") or item.get("category")) in categories
        ]
    if predicate.event_types:
        result = [item for item in result if _matches_event_types(item, predicate.event_types)]
    if predicate.counterparty is not None:
        result = [item for item in result if _matches_counterparty(item, predicate.counterparty)]
    if predicate.narration is not None:
        query = _normalized_text(predicate.narration.query)
        result = [item for item in result if query in _normalized_text(item.get("narration"))]
    if predicate.exclusions:
        exclusions = [_normalized_text(item) for item in predicate.exclusions]
        result = [
            item for item in result if not any(term in _normalized_text(item.get("narration")) for term in exclusions)
        ]
    return result
