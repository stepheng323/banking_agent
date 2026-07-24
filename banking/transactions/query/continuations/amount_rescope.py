"""Amount-rescope recovery for active query continuations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from banking.transactions.query.continuations.transforms import rebuild_query_request
from banking.transactions.query.models.domain import Filters, QueryRequest


@dataclass(frozen=True)
class AmountFilterPatch:
    min_amount: float | None = None
    max_amount: float | None = None
    min_amount_inclusive: bool = True
    max_amount_inclusive: bool = True


_PREFIX_RE = re.compile(r"^(?:what about|how about|what of|only|just|make it|change it to)\s+", re.IGNORECASE)
_AMOUNT_RE = re.compile(
    r"^(?P<operator>above|over|more than|greater than|at least|minimum(?: of)?|"
    r"below|under|less than|lower than|at most|maximum(?: of)?|up to|exactly|equal to)?\s*"
    r"(?:₦|ngn\s*)?(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<suffix>k|m|naira|ngn)?$",
    re.IGNORECASE,
)


def amount_rescope_filter_patch(message: str) -> AmountFilterPatch | None:
    """Return an amount filter patch for amount-only active-query follow-ups."""
    normalized = " ".join(message.strip().lower().split()).rstrip("?.!,")
    if not normalized:
        return None

    normalized = _PREFIX_RE.sub("", normalized).strip()
    match = _AMOUNT_RE.fullmatch(normalized)
    if match is None:
        return None

    amount = _parse_amount(match.group("amount"), match.group("suffix"))
    if amount is None:
        return None

    operator = " ".join((match.group("operator") or "").split())
    if operator in {"below", "under", "less than", "lower than"}:
        return AmountFilterPatch(max_amount=amount, max_amount_inclusive=False)
    if operator in {"at most", "maximum", "maximum of", "up to"}:
        return AmountFilterPatch(max_amount=amount, max_amount_inclusive=True)
    if operator in {"exactly", "equal to"}:
        return AmountFilterPatch(min_amount=amount, max_amount=amount)
    if operator in {"at least", "minimum", "minimum of"}:
        return AmountFilterPatch(min_amount=amount, min_amount_inclusive=True)
    return AmountFilterPatch(min_amount=amount, min_amount_inclusive=False)


def rebuild_with_amount_rescope(
    session_query_request: QueryRequest,
    *,
    patch: AmountFilterPatch,
    continuation_type: str,
) -> QueryRequest:
    filters = Filters(
        min_amount=patch.min_amount,
        max_amount=patch.max_amount,
        min_amount_inclusive=patch.min_amount_inclusive,
        max_amount_inclusive=patch.max_amount_inclusive,
    )
    return rebuild_query_request(
        session_query_request,
        filters=filters,
        merge_filters=True,
        result_limit=None,
        result_reference=None,
        answer_fact_field=None,
        continuation_type=continuation_type,
        continuation_delta_type="amount",
    )


def _parse_amount(raw_amount: str, suffix: str | None) -> float | None:
    try:
        amount = Decimal(raw_amount.replace(",", ""))
    except Exception:
        return None
    suffix = (suffix or "").strip().lower()
    if suffix == "k":
        amount *= Decimal("1000")
    elif suffix == "m":
        amount *= Decimal("1000000")
    return float(amount) if amount > 0 else None
