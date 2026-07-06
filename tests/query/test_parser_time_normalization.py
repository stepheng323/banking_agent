from datetime import date

from banking.transactions.query.models.extraction import (
    QueryAggregation,
    QueryExtractionResult,
    QueryIntent,
    QueryTimeRange,
    TimeReference,
)
from banking.transactions.query.services.parsing.parser import QueryParser


class _DummyLLM:
    def with_structured_output(self, schema: object) -> object:
        del schema
        raise NotImplementedError


def test_today_period_uses_same_day_window() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 3)
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today", days_back=0),
        raw_query="how much have i spent today",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    assert query_ir.time_range.start == today
    assert query_ir.time_range.end == today


def test_yesterday_period_uses_previous_day_window() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 3)
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="yesterday"),
        raw_query="how much did i spend yesterday",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    assert query_ir.time_range.start == date(2026, 3, 2)
    assert query_ir.time_range.end == date(2026, 3, 2)


def test_days_back_zero_is_not_defaulted_to_thirty_days() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 3)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, days_back=0),
        raw_query="show my transactions for today",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    assert query_ir.time_range.start == today
    assert query_ir.time_range.end == today


def test_received_query_keeps_credit_filter() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 3)
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today"),
        raw_query="how much have i received today",
    )

    contract = parser.build_execution_contract_from_ir(
        parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    )
    assert contract.filters is not None
    assert contract.filters.transaction_type == "credit"


def test_non_aggregate_spend_phrase_stays_transaction_list() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 6)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today", days_back=0),
        raw_query="show my spent transactions today",
    )

    contract = parser.build_execution_contract_from_ir(
        parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    )

    assert contract.intent == QueryIntent.TRANSACTION_LIST


def test_spending_total_defaults_transaction_type_to_debit() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 6)
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today", days_back=0),
        raw_query="how much did I spend today",
    )

    contract = parser.build_execution_contract_from_ir(
        parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    )

    assert contract.filters is not None
    assert contract.filters.transaction_type == "debit"


def test_credit_keywords_take_precedence_over_spending_keywords() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 6)
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today", days_back=0),
        raw_query="how much salary did I receive and spend today",
    )

    contract = parser.build_execution_contract_from_ir(
        parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    )

    assert contract.filters is not None
    assert contract.filters.transaction_type == "credit"


def test_singular_largest_expense_defaults_limit_to_one() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 6)
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month", days_back=30),
        raw_query="what is my largest expense this month",
    )

    contract = parser.build_execution_contract_from_ir(
        parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    )

    assert contract.aggregation is not None
    assert contract.aggregation.type == "largest"
    assert contract.aggregation.limit == 1


def test_singular_smallest_transaction_overrides_provided_limit() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 6)
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month", days_back=30),
        aggregation=QueryAggregation(type="smallest", limit=7),
        raw_query="show my smallest transaction this month",
    )

    contract = parser.build_execution_contract_from_ir(
        parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    )

    assert contract.aggregation is not None
    assert contract.aggregation.type == "smallest"
    assert contract.aggregation.limit == 1


def test_named_current_month_defaults_to_current_year_to_date() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="march"),
        raw_query="show all march transactions",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")

    assert query_ir.time_range.start == date(2026, 3, 1)
    assert query_ir.time_range.end == today


def test_named_future_month_defaults_to_previous_year() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="december"),
        raw_query="show all december transactions",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")

    assert query_ir.time_range.start == date(2025, 12, 1)
    assert query_ir.time_range.end == date(2025, 12, 31)


def test_named_month_last_year_defaults_to_previous_year_month() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="march_last_year"),
        raw_query="show all march last year transactions",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")

    assert query_ir.time_range.start == date(2025, 3, 1)
    assert query_ir.time_range.end == date(2025, 3, 31)


def test_income_vs_spending_breakdown_keeps_unfiltered_transaction_type() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        aggregation=QueryAggregation(type="breakdown", group_by="transaction_type"),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="compare income vs spending this month",
    )

    contract = parser.build_execution_contract_from_ir(
        parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    )

    assert contract.intent == QueryIntent.ANALYTICS_SUMMARY
    assert contract.aggregation is not None
    assert contract.aggregation.type == "breakdown"
    assert contract.aggregation.group_by == "transaction_type"
    assert contract.filters is not None
    assert contract.filters.transaction_type is None
