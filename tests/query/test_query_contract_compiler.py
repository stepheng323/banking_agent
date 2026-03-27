from datetime import date

from apps.core.src.agent.graphs.query.models import (
    ExtractionIntent,
    QueryAggregation,
    QueryComparison,
    QueryExecutionContract,
    QueryExtractionResult,
    QueryFilters,
    QueryIntent,
    QueryOperation,
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


def test_explicit_time_comparison_extraction_compiles_to_time_comparison() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 6)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TIME_COMPARISON,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month", days_back=30),
        raw_query="compare my spending this month vs last month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.intent == QueryIntent.TIME_COMPARISON
    assert contract.intent == QueryIntent.TIME_COMPARISON


def test_explicit_beneficiary_summary_extraction_compiles_count_ranking() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 7)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.BENEFICIARY_SUMMARY,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_week", days_back=5),
        aggregation=QueryAggregation(type="sum", sort_by="count"),
        raw_query="Who did I send money to the most this week",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert contract.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_ir.aggregation is not None
    assert query_ir.aggregation.sort_by == "count"
    assert contract.aggregation is not None
    assert contract.aggregation.sort_by == "count"
    assert query_ir.time_range.start == date(2026, 3, 2)
    assert query_ir.time_range.end == today


def test_query_operation_sum_hint_compiles_to_analytics_summary() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 6)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        query_operation=QueryOperation.SUM_TRANSACTIONS,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today", days_back=0),
        raw_query="how much did I spend today",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.query_operation == QueryOperation.SUM_TRANSACTIONS
    assert query_ir.intent == QueryIntent.ANALYTICS_SUMMARY
    assert contract.query_operation == QueryOperation.SUM_TRANSACTIONS
    assert contract.intent == QueryIntent.ANALYTICS_SUMMARY
    assert contract.aggregation is not None
    assert contract.aggregation.type == "sum"


def test_query_operation_beneficiary_hint_compiles_to_beneficiary_summary() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        query_operation=QueryOperation.SUMMARIZE_BENEFICIARIES,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="who did I send money to this month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.query_operation == QueryOperation.SUMMARIZE_BENEFICIARIES
    assert query_ir.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert contract.query_operation == QueryOperation.SUMMARIZE_BENEFICIARIES
    assert contract.intent == QueryIntent.BENEFICIARY_SUMMARY


def test_query_operation_breakdown_hint_preserves_transaction_type_grouping() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        query_operation=QueryOperation.BREAKDOWN_TRANSACTIONS,
        aggregation=QueryAggregation(type="breakdown", group_by="transaction_type"),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="compare the income vs spending",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.query_operation == QueryOperation.BREAKDOWN_TRANSACTIONS
    assert query_ir.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_ir.aggregation is not None
    assert query_ir.aggregation.type == "breakdown"
    assert query_ir.aggregation.group_by == "transaction_type"
    assert contract.aggregation is not None
    assert contract.aggregation.group_by == "transaction_type"


def test_explicit_amount_ranked_beneficiary_summary_compiles_amount_sort() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.BENEFICIARY_SUMMARY,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        aggregation=QueryAggregation(type="sum", sort_by="amount"),
        raw_query="Who got the most money this month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert contract.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_ir.aggregation is not None
    assert query_ir.aggregation.sort_by == "amount"
    assert contract.aggregation is not None
    assert contract.aggregation.sort_by == "amount"


def test_parser_does_not_lexically_upgrade_plain_recipient_summary_text() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="Who did I send money to this month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.intent == QueryIntent.TRANSACTION_LIST
    assert contract.intent == QueryIntent.TRANSACTION_LIST


def test_parser_does_not_lexically_upgrade_comparison_text() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="compare my spending this month vs last month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.intent == QueryIntent.TRANSACTION_LIST
    assert contract.intent == QueryIntent.TRANSACTION_LIST


def test_explicit_largest_transfer_extraction_compiles_to_largest_analytics_query() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.SPENDING_TOTAL,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        aggregation=QueryAggregation(type="largest", limit=1),
        result_reference="latest",
        raw_query="Whats my highest single transfer this month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.intent == QueryIntent.ANALYTICS_SUMMARY
    assert contract.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_ir.filters is not None
    assert query_ir.filters.transaction_type == "debit"
    assert query_ir.aggregation is not None
    assert query_ir.aggregation.type == "largest"
    assert query_ir.aggregation.limit == 1
    assert query_ir.result_reference is None
    assert contract.normalized_query.result_reference is None


def test_parser_does_not_lexically_upgrade_highest_single_transfer_text() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        result_reference="latest",
        raw_query="Whats my most single transfer this month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.intent == QueryIntent.TRANSACTION_LIST
    assert contract.intent == QueryIntent.TRANSACTION_LIST
    assert query_ir.result_reference == "latest"
    assert contract.normalized_query.result_reference == "latest"


def test_explicit_this_week_without_days_back_compiles_to_calendar_week_to_date() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 19)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.SPENDING_TOTAL,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_week"),
        raw_query="how much did I spend this week",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.time_range.start == date(2026, 3, 16)
    assert query_ir.time_range.end == today
    assert contract.time_start == date(2026, 3, 16)
    assert contract.time_end == today


def test_explicit_last_month_without_days_back_compiles_to_full_previous_month() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 19)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_month"),
        raw_query="show my transactions last month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.time_range.start == date(2026, 2, 1)
    assert query_ir.time_range.end == date(2026, 2, 28)
    assert contract.time_start == date(2026, 2, 1)
    assert contract.time_end == date(2026, 2, 28)


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
    assert query_ir.comparison.explicit_range.end == date(2026, 2, 6)
    assert contract.comparison is not None
    assert contract.comparison.mode == "explicit_range"


def test_structured_comparison_last_month_aligns_to_current_window_duration() -> None:
    parser = QueryParser(_DummyLLM())
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TIME_COMPARISON,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month", days_back=6),
        comparison=QueryComparison(mode="explicit_period", period="last_month"),
        raw_query="compare this month vs last month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=date(2026, 3, 7), language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.comparison is not None
    assert query_ir.comparison.mode == "explicit_range"
    assert query_ir.comparison.explicit_range is not None
    assert query_ir.comparison.explicit_range.start == date(2026, 2, 1)
    assert query_ir.comparison.explicit_range.end == date(2026, 2, 7)
    assert contract.comparison is not None
    assert contract.comparison.mode == "explicit_range"


def test_structured_comparison_last_week_compiles_to_explicit_range() -> None:
    parser = QueryParser(_DummyLLM())
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TIME_COMPARISON,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_week", days_back=7),
        comparison=QueryComparison(mode="explicit_period", period="last_week"),
        raw_query="compare this week vs last week",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=date(2026, 3, 6), language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.comparison is not None
    assert query_ir.comparison.mode == "explicit_range"
    assert query_ir.comparison.explicit_range is not None
    assert query_ir.comparison.explicit_range.start == date(2026, 2, 23)
    assert query_ir.comparison.explicit_range.end == date(2026, 2, 27)
    assert contract.comparison is not None
    assert contract.comparison.mode == "explicit_range"


def test_structured_comparison_last_week_aligns_to_current_window_duration() -> None:
    parser = QueryParser(_DummyLLM())
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TIME_COMPARISON,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_week", days_back=4),
        comparison=QueryComparison(mode="explicit_period", period="last_week"),
        raw_query="compare this week vs last week",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=date(2026, 3, 6), language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.comparison is not None
    assert query_ir.comparison.mode == "explicit_range"
    assert query_ir.comparison.explicit_range is not None
    assert query_ir.comparison.explicit_range.start == date(2026, 2, 23)
    assert query_ir.comparison.explicit_range.end == date(2026, 2, 27)
    assert contract.comparison is not None
    assert contract.comparison.mode == "explicit_range"


def test_structured_comparison_explicit_period_handles_leap_february() -> None:
    parser = QueryParser(_DummyLLM())
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TIME_COMPARISON,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month", days_back=30),
        comparison=QueryComparison(mode="explicit_period", period="last_month"),
        raw_query="compare this month with last month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=date(2024, 3, 6), language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.comparison is not None
    assert query_ir.comparison.mode == "explicit_range"
    assert query_ir.comparison.explicit_range is not None
    assert query_ir.comparison.explicit_range.start == date(2024, 2, 1)
    assert query_ir.comparison.explicit_range.end == date(2024, 2, 6)
    assert contract.comparison is not None
    assert contract.comparison.mode == "explicit_range"


def test_structured_comparison_invalid_explicit_period_falls_back_to_previous_equivalent() -> None:
    parser = QueryParser(_DummyLLM())
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.TIME_COMPARISON,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month", days_back=30),
        comparison=QueryComparison(mode="explicit_period", period="banana_week"),
        raw_query="compare this month to banana week",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=date(2026, 3, 6), language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.comparison is not None
    assert query_ir.comparison.mode == "previous_equivalent"
    assert query_ir.comparison.explicit_range is None
    assert contract.comparison is not None
    assert contract.comparison.mode == "previous_equivalent"


def test_recipient_queries_compile_to_counterparty_filter() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.SINGLE_TRANSACTION,
        filters=QueryFilters(recipient="Mum", transaction_type="debit"),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="when did I last pay Mum this month",
        result_limit=1,
        result_reference="latest",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.filters is not None
    assert query_ir.filters.counterparty == ["Mum"]
    assert query_ir.filters.merchant is None
    assert contract.normalized_query.answer_fact_field == "date"


def test_who_sent_me_query_sets_counterparty_answer_fact() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=ExtractionIntent.SINGLE_TRANSACTION,
        filters=QueryFilters(min_amount=500000, max_amount=500000),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
        raw_query="who sent me 500k last week",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")

    assert query_ir.filters is not None
    assert query_ir.filters.transaction_type == "credit"
    assert query_ir.answer_fact_field == "counterparty"
