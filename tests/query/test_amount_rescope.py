from datetime import date

from banking.transactions.query.continuations.amount_rescope import (
    amount_rescope_filter_patch,
    rebuild_with_amount_rescope,
)
from banking.transactions.query.models.domain import Filters, QueryExecutionContract, QueryIntent, QueryIR, TimeRange


def test_amount_rescope_parses_strict_lower_bound_followup() -> None:
    patch = amount_rescope_filter_patch("What about above 4k")

    assert patch is not None
    assert patch.min_amount == 4000
    assert patch.min_amount_inclusive is False
    assert patch.max_amount is None


def test_amount_rescope_preserves_existing_scope_and_replaces_amount() -> None:
    base = QueryExecutionContract.from_query_ir(
        QueryIR(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=Filters(counterparty=["Tolu"], min_amount=50000, min_amount_inclusive=False),
            time_range=TimeRange(start=date(2026, 6, 1), end=date(2026, 6, 27)),
        )
    )
    patch = amount_rescope_filter_patch("What about above 4k")
    assert patch is not None

    rebuilt = rebuild_with_amount_rescope(base, patch=patch, continuation_type="unclear")

    assert rebuilt.filters is not None
    assert rebuilt.filters.counterparty == ["Tolu"]
    assert rebuilt.filters.min_amount == 4000
    assert rebuilt.filters.min_amount_inclusive is False
    assert rebuilt.time_range is not None
    assert rebuilt.time_range.start == date(2026, 6, 1)
