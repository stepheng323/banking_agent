"""Regression tests for locale-safe query formatter behavior."""

from datetime import date

from apps.core.src.agent.graphs.query.models import QueryResult, QueryResultItem, ResultSurface, SurfaceType
from apps.core.src.agent.graphs.query.services.formatter import QueryFormatter


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
