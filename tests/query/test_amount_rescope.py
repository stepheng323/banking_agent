from datetime import date

from banking.transactions.query.continuations.amount_rescope import (
    amount_rescope_filter_patch,
    rebuild_with_amount_rescope,
)
from banking.transactions.query.models.operations import (
    AmountRange,
    CounterpartySelector,
    Money,
    NamedCounterparty,
    TransactionPredicate,
)
from tests.query.factories import query_scope, retrieve_request


def test_amount_rescope_parses_strict_lower_bound_followup() -> None:
    patch = amount_rescope_filter_patch("What about above 4k")

    assert patch is not None
    assert patch.min_amount == 4000
    assert patch.min_amount_inclusive is False
    assert patch.max_amount is None


def test_amount_rescope_preserves_existing_scope_and_replaces_amount() -> None:
    base = retrieve_request(
        query_scope(
            date(2026, 6, 1),
            date(2026, 6, 27),
            predicate=TransactionPredicate(
                counterparty=CounterpartySelector(role="recipient", reference=NamedCounterparty(name="Tolu")),
                amount=AmountRange(minimum=Money(amount=50000), minimum_inclusive=False),
            ),
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
