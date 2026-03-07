from datetime import date

from apps.core.src.agent.graphs.query.models import (
    ExtractionIntent,
    QueryAggregation,
    QueryExtractionResult,
    QueryIntent,
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
    assert normalized.time_range.end == date(2026, 3, 2)


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


def test_targeted_spend_total_cue_forces_analytics_summary_on_list_misclassification() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 6)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today", days_back=0),
        raw_query="How much did I spend today",
    )

    normalized = parser.convert_to_normalized(extraction, today=today)

    assert normalized.intent == QueryIntent.ANALYTICS_SUMMARY
    assert normalized.aggregation is not None
    assert normalized.aggregation.type == "sum"
    assert normalized.time_range is not None
    assert normalized.time_range.start == today
    assert normalized.time_range.end == today


def test_non_aggregate_spend_phrase_stays_transaction_list() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 6)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today", days_back=0),
        raw_query="show my spent transactions today",
    )

    normalized = parser.convert_to_normalized(extraction, today=today)

    assert normalized.intent == QueryIntent.TRANSACTION_LIST


def test_spending_total_defaults_transaction_type_to_debit() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 6)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.SPENDING_TOTAL,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today", days_back=0),
        raw_query="how much did I spend today",
    )

    normalized = parser.convert_to_normalized(extraction, today=today)

    assert normalized.filters is not None
    assert normalized.filters.transaction_type == "debit"


def test_credit_keywords_take_precedence_over_spending_keywords() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 6)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.SPENDING_TOTAL,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today", days_back=0),
        raw_query="how much salary did I receive and spend today",
    )

    normalized = parser.convert_to_normalized(extraction, today=today)

    assert normalized.filters is not None
    assert normalized.filters.transaction_type == "credit"


def test_singular_largest_expense_defaults_limit_to_one() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 6)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.SPENDING_TOTAL,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month", days_back=30),
        raw_query="what is my largest expense this month",
    )

    normalized = parser.convert_to_normalized(extraction, today=today)

    assert normalized.aggregation is not None
    assert normalized.aggregation.type == "largest"
    assert normalized.aggregation.limit == 1


def test_singular_smallest_transaction_overrides_provided_limit() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 6)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.SPENDING_TOTAL,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month", days_back=30),
        aggregation=QueryAggregation(type="smallest", limit=7),
        raw_query="show my smallest transaction this month",
    )

    normalized = parser.convert_to_normalized(extraction, today=today)

    assert normalized.aggregation is not None
    assert normalized.aggregation.type == "smallest"
    assert normalized.aggregation.limit == 1
