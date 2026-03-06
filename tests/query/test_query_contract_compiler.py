from datetime import date

from apps.core.src.agent.graphs.query.models import (
    ExtractionIntent,
    QueryComparison,
    QueryExecutionContract,
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


def test_build_query_contract_from_extraction_preserves_lagos_today_window() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 6)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.SPENDING_TOTAL,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today", days_back=0),
        raw_query="how much did I spend today",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert contract.time_start == today
    assert contract.time_end == today
    assert contract.intent == QueryIntent.ANALYTICS_SUMMARY
    assert contract.normalized_query.intent == QueryIntent.ANALYTICS_SUMMARY


def test_targeted_comparison_cue_upgrades_misclassified_list_to_time_comparison() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 6)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month", days_back=30),
        raw_query="compare my spending this month vs last month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.intent == QueryIntent.TIME_COMPARISON
    assert contract.intent == QueryIntent.TIME_COMPARISON


def test_contract_compiles_from_legacy_normalized_query() -> None:
    parser = QueryParser(_DummyLLM())
    normalized = parser.convert_to_normalized(
        QueryExtractionResult(
            intent=ExtractionIntent.TRANSACTION_LIST,
            time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="yesterday"),
            raw_query="show my transactions yesterday",
        ),
        today=date(2026, 3, 6),
    )

    contract = QueryExecutionContract.from_normalized_query(normalized)

    assert contract.time_start == date(2026, 3, 5)
    assert contract.time_end == date(2026, 3, 5)
    assert contract.normalized_query.time_range is not None
    assert contract.normalized_query.time_range.start == date(2026, 3, 5)


def test_structured_comparison_year_ago_compiles_to_contract() -> None:
    parser = QueryParser(_DummyLLM())
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TIME_COMPARISON,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month", days_back=30),
        comparison=QueryComparison(mode="year_ago"),
        raw_query="compare this month to same period last year",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=date(2026, 3, 6), language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.comparison is not None
    assert query_ir.comparison.mode == "year_ago"
    assert contract.comparison is not None
    assert contract.comparison.mode == "year_ago"


def test_structured_comparison_explicit_period_compiles_to_explicit_range() -> None:
    parser = QueryParser(_DummyLLM())
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TIME_COMPARISON,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month", days_back=30),
        comparison=QueryComparison(mode="explicit_period", period="last_month"),
        raw_query="compare this month vs last month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=date(2026, 3, 6), language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.comparison is not None
    assert query_ir.comparison.mode == "explicit_range"
    assert query_ir.comparison.explicit_range is not None
    assert query_ir.comparison.explicit_range.start == date(2026, 2, 1)
    assert query_ir.comparison.explicit_range.end == date(2026, 2, 28)
    assert contract.comparison is not None
    assert contract.comparison.mode == "explicit_range"
