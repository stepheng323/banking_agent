"""Regression tests for locale-safe query formatter behavior."""

from datetime import date, timedelta

from apps.core.src.agent.graphs.query.models import (
    Filters,
    NormalizedQuery,
    QueryIntent,
    QueryResult,
    QueryResultItem,
    ResultSurface,
    SurfaceType,
    TimeRange,
)
from apps.core.src.agent.graphs.query.services.formatter import QueryFormatter
from apps.core.src.agent.graphs.query.utils.timezone import lagos_today


def test_formatter_returns_summary_for_summary_surface() -> None:
    result = QueryResult(
        summary_text="Akopọ inawo rẹ",
        items=[
            QueryResultItem(
                id="tx1",
                description="Transfer",
                amount=1000,
                date=date.today(),
                metadata={"type": "debit"},
            )
        ],
        surface=ResultSurface(type=SurfaceType.SUMMARY, items=[{"key": "total", "amount": 1000, "count": 1}]),
    )

    assert QueryFormatter.format(result, locale="yo") == "Akopọ inawo rẹ"


def test_formatter_returns_summary_when_no_items() -> None:
    result = QueryResult(summary_text="Ko si transaction to baamu.")

    assert QueryFormatter.format(result, locale="yo") == "Ko si transaction to baamu."


def test_formatter_uses_breakdown_surface_without_english_prefix() -> None:
    result = QueryResult(
        summary_text="Ìtúpalẹ̀ nípasẹ̀ merchant",
        items=[
            QueryResultItem(
                id="1",
                description="food",
                amount=2000,
                date=date.today(),
                metadata={"count": 2},
            )
        ],
        surface=ResultSurface(
            type=SurfaceType.BREAKDOWN,
            items=[{"id": "1", "key": "food", "amount": 2000, "count": 2}],
        ),
    )

    response = QueryFormatter.format(result, locale="en")
    assert "Ìtúpalẹ̀ nípasẹ̀ merchant" in response
    assert "Food" in response


def test_formatter_uses_rank_metadata_without_english_summary() -> None:
    result = QueryResult(
        summary_text="Akopọ ipo inawo",
        items=[
            QueryResultItem(
                id="r1",
                description="Groceries",
                amount=5000,
                date=date.today(),
                metadata={"rank": 1, "type": "debit"},
            ),
            QueryResultItem(
                id="r2",
                description="Fuel",
                amount=3000,
                date=date.today(),
                metadata={"rank": 2, "type": "debit"},
            ),
        ],
    )

    response = QueryFormatter.format(result, locale="yo")
    assert "🏆" in response
    assert "1." in response


def test_formatter_no_results_with_type_for_today() -> None:
    today = lagos_today()
    result = QueryResult(
        summary_text="",
        items=[],
        query_snapshot=NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=Filters(transaction_type="credit"),
            time_range=TimeRange(start=today, end=today),
        ),
    )

    assert QueryFormatter.format(result, locale="en") == "No credit transactions found today."


def test_formatter_no_results_with_type_for_period() -> None:
    today = date.today()
    result = QueryResult(
        summary_text="",
        items=[],
        query_snapshot=NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=Filters(transaction_type="debit"),
            time_range=TimeRange(start=today - timedelta(days=7), end=today - timedelta(days=1)),
        ),
    )

    assert QueryFormatter.format(result, locale="en") == "No debit transactions found for this period."


def test_formatter_no_results_without_type_uses_generic_message() -> None:
    result = QueryResult(
        summary_text="",
        items=[],
        query_snapshot=NormalizedQuery(intent=QueryIntent.TRANSACTION_LIST),
    )

    assert QueryFormatter.format(result, locale="en") == "No matching transactions found for your search."


def _sample_list_result(query_snapshot: NormalizedQuery) -> QueryResult:
    return QueryResult(
        summary_text="accounts:1|showing:1-2|total:2",
        items=[
            QueryResultItem(
                id="tx1",
                description="Transfer to Ada",
                amount=2000,
                date=date.today(),
                metadata={"type": "debit", "bank_name": "Zenith"},
            ),
            QueryResultItem(
                id="tx2",
                description="Salary",
                amount=10000,
                date=date.today(),
                metadata={"type": "credit", "bank_name": "Zenith"},
            ),
        ],
        query_snapshot=query_snapshot,
    )


def test_formatter_heading_uses_credit_context() -> None:
    result = _sample_list_result(
        NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=Filters(transaction_type="credit"),
        )
    )

    response = QueryFormatter.format(result, locale="en")
    assert response.splitlines()[0] == "*Credit Transactions*"


def test_formatter_heading_uses_category_spending_for_debit() -> None:
    result = _sample_list_result(
        NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=Filters(transaction_type="debit", category=["food"]),
        )
    )

    response = QueryFormatter.format(result, locale="en")
    assert response.splitlines()[0] == "*Food Spending*"


def test_formatter_heading_appends_account_and_today_suffix() -> None:
    today = lagos_today()
    result = _sample_list_result(
        NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=Filters(account_filter="Zenith"),
            time_range=TimeRange(start=today, end=today),
        )
    )

    response = QueryFormatter.format(result, locale="en")
    assert response.splitlines()[0] == "*Transactions* — Zenith — Today"


def test_formatter_heading_single_day_past_range_is_not_labeled_today() -> None:
    today = date.today()
    past_day = today - timedelta(days=1)
    result = _sample_list_result(
        NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=past_day, end=past_day),
        )
    )

    response = QueryFormatter.format(result, locale="en")
    heading = response.splitlines()[0]
    past_label = past_day.strftime("%b %d").replace(" 0", " ")
    assert heading == f"*Transactions* — {past_label}–{past_label}"
    assert "Today" not in heading
