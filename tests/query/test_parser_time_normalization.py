from datetime import date

from apps.core.src.agent.graphs.query.models import (
    ExtractionIntent,
    QueryExtractionResult,
    QueryTimeRange,
    TimeReference,
)
from apps.core.src.agent.graphs.query.services.parser import QueryParser


class _DummyLLM:
    def with_structured_output(self, schema: object) -> object:
        del schema
        raise NotImplementedError


def test_today_period_uses_same_day_window() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 3)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.SPENDING_TOTAL,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today", days_back=0),
        raw_query="how much have i spent today",
    )

    normalized = parser.convert_to_normalized(extraction, today=today)
    assert normalized.time_range is not None
    assert normalized.time_range.start == today
    assert normalized.time_range.end == today


def test_yesterday_period_uses_previous_day_window() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 3)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.SPENDING_TOTAL,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="yesterday"),
        raw_query="how much did i spend yesterday",
    )

    normalized = parser.convert_to_normalized(extraction, today=today)
    assert normalized.time_range is not None
    assert normalized.time_range.start == date(2026, 3, 2)
    assert normalized.time_range.end == today


def test_days_back_zero_is_not_defaulted_to_thirty_days() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 3)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, days_back=0),
        raw_query="show my transactions for today",
    )

    normalized = parser.convert_to_normalized(extraction, today=today)
    assert normalized.time_range is not None
    assert normalized.time_range.start == today
    assert normalized.time_range.end == today


def test_received_query_keeps_credit_filter() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 3)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.SPENDING_TOTAL,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today"),
        raw_query="how much have i received today",
    )

    normalized = parser.convert_to_normalized(extraction, today=today)
    assert normalized.filters is not None
    assert normalized.filters.transaction_type == "credit"

