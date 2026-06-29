from datetime import date

import pytest

from banking.transactions.query.models.domain import QueryExecutionContract
from banking.transactions.query.models.extraction import (
    FactQueryKind,
    QueryAggregation,
    QueryComparison,
    QueryExtractionResult,
    QueryFilters,
    QueryIntent,
    QueryRequestShape,
    QueryTimeRange,
    TimeReference,
)
from banking.transactions.query.services.parsing.parser import QueryParser


class _DummyLLM:
    def with_structured_output(self, schema: object) -> object:
        del schema
        raise NotImplementedError


def test_build_query_contract_from_extraction_preserves_lagos_today_window() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 6)
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today", days_back=0),
        raw_query="how much did I spend today",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert contract.time_start == today
    assert contract.time_end == today
    assert contract.intent == QueryIntent.ANALYTICS_SUMMARY
    assert contract.intent == QueryIntent.ANALYTICS_SUMMARY


def test_explicit_time_comparison_extraction_compiles_to_time_comparison() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 6)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TIME_COMPARISON,
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
        intent=QueryIntent.BENEFICIARY_SUMMARY,
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




def test_intent_beneficiary_hint_compiles_to_beneficiary_summary() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="who did I send money to this month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert contract.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_ir.aggregation is not None
    assert query_ir.aggregation.sort_by == "amount"
    assert query_ir.result_limit is None
    assert contract.aggregation is not None
    assert contract.aggregation.sort_by == "amount"
    assert contract.result_limit is None


def test_incoming_beneficiary_hint_compiles_to_amount_ranked_grouped_summary() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="who sent me money this month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_ir.filters is not None
    assert query_ir.filters.transaction_type == "credit"
    assert query_ir.aggregation is not None
    assert query_ir.aggregation.sort_by == "amount"
    assert query_ir.result_limit is None
    assert contract.aggregation is not None
    assert contract.aggregation.sort_by == "amount"
    assert contract.result_limit is None




def test_explicit_amount_ranked_beneficiary_summary_compiles_amount_sort() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
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


def test_parser_lexically_recovers_plain_recipient_summary_text() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="Who did I send money to this month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert contract.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_ir.filters.counterparty is None
    assert contract.filters.counterparty is None
    assert query_ir.filters.transaction_type == "debit"
    assert contract.filters.transaction_type == "debit"


def test_parser_does_not_lexically_upgrade_comparison_text() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
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
        intent=QueryIntent.ANALYTICS_SUMMARY,
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
    assert contract.result_reference is None


def test_parser_does_not_lexically_upgrade_highest_single_transfer_text() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        result_reference="latest",
        raw_query="Whats my most single transfer this month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.intent == QueryIntent.TRANSACTION_LIST
    assert contract.intent == QueryIntent.TRANSACTION_LIST
    assert query_ir.result_reference == "latest"
    assert contract.result_reference == "latest"


def test_explicit_this_week_without_days_back_compiles_to_calendar_week_to_date() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 19)
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_week"),
        raw_query="how much did I spend this week",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.time_range.start == date(2026, 3, 16)
    assert query_ir.time_range.end == today
    assert contract.time_start == date(2026, 3, 16)
    assert contract.time_end == today


def test_explicit_this_month_without_days_back_compiles_to_calendar_month_to_date() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 6, 27)
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month", days_back=30),
        raw_query="how much did I spend this month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.time_range.start == date(2026, 6, 1)
    assert query_ir.time_range.end == today
    assert contract.time_start == date(2026, 6, 1)
    assert contract.time_end == today


def test_came_in_query_compiles_to_credit_sum_only() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 6, 27)
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="How much came in this month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_ir.filters is not None
    assert query_ir.filters.transaction_type == "credit"
    assert contract.filters is not None
    assert contract.filters.transaction_type == "credit"


def test_failed_transaction_query_compiles_to_status_filter() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 6, 27)
    result = parser.parse_deterministic("Show failed transaction for this month", today=today, language="en")

    assert result is not None
    contract = QueryExecutionContract.model_validate(result.query_contract)
    assert contract.intent == QueryIntent.TRANSACTION_LIST
    assert contract.filters is not None
    assert contract.filters.status == "failed"
    assert contract.time_start == date(2026, 6, 1)
    assert contract.time_end == today


def test_can_i_send_amount_compiles_to_affordability_amount_check() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 6, 27)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        raw_query="Can I send 100k?",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.intent == QueryIntent.AFFORDABILITY
    assert query_ir.amount_check == 100000
    assert contract.intent == QueryIntent.AFFORDABILITY
    assert contract.amount_check == 100000


def test_cashflow_by_account_compiles_to_account_breakdown() -> None:
    parser = QueryParser(_DummyLLM())
    result = parser.parse_deterministic("Breakdown my cash flow by account", today=date(2026, 6, 27), language="en")

    assert result is not None
    contract = QueryExecutionContract.model_validate(result.query_contract)
    assert contract.intent == QueryIntent.CASH_FLOW_SUMMARY
    assert contract.aggregation is not None
    assert contract.aggregation.group_by == "account"


def test_where_did_my_money_go_compiles_to_category_breakdown() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 6, 27)
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="Where did my money go this month?",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.aggregation is not None
    assert query_ir.aggregation.type == "breakdown"
    assert query_ir.aggregation.group_by == "category"
    assert query_ir.filters is not None
    assert query_ir.filters.transaction_type == "debit"
    assert contract.aggregation is not None
    assert contract.aggregation.type == "breakdown"


def test_amount_filter_above_is_strict_and_and_above_is_inclusive() -> None:
    parser = QueryParser(_DummyLLM())
    strict_ir = parser.build_query_ir_from_extraction(
        QueryExtractionResult(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=QueryFilters(transaction_type="debit", min_amount=50000),
            raw_query="show debits above 50k",
        ),
        today=date(2026, 6, 27),
        language="en",
    )
    inclusive_ir = parser.build_query_ir_from_extraction(
        QueryExtractionResult(
            intent=QueryIntent.TRANSACTION_LIST,
            filters=QueryFilters(transaction_type="debit", min_amount=50000),
            raw_query="show debits 50k and above",
        ),
        today=date(2026, 6, 27),
        language="en",
    )

    assert strict_ir.filters is not None
    assert strict_ir.filters.min_amount == 50000
    assert strict_ir.filters.min_amount_inclusive is False
    assert inclusive_ir.filters is not None
    assert inclusive_ir.filters.min_amount == 50000
    assert inclusive_ir.filters.min_amount_inclusive is True


def test_explicit_last_month_without_days_back_compiles_to_full_previous_month() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 19)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_month"),
        raw_query="show my transactions last month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.time_range.start == date(2026, 2, 1)
    assert query_ir.time_range.end == date(2026, 2, 28)
    assert contract.time_start == date(2026, 2, 1)
    assert contract.time_end == date(2026, 2, 28)


def test_contract_compiles_from_query_ir() -> None:
    parser = QueryParser(_DummyLLM())
    query_ir = parser.build_query_ir_from_extraction(
        QueryExtractionResult(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="yesterday"),
            raw_query="show my transactions yesterday",
        ),
        today=date(2026, 3, 6),
        language="en",
    )
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert contract.time_start == date(2026, 3, 5)
    assert contract.time_end == date(2026, 3, 5)
    assert contract.time_range is not None
    assert contract.time_range.start == date(2026, 3, 5)


def test_structured_comparison_year_ago_compiles_to_contract() -> None:
    parser = QueryParser(_DummyLLM())
    extraction = QueryExtractionResult(
        intent=QueryIntent.TIME_COMPARISON,
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
        intent=QueryIntent.TIME_COMPARISON,
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
        intent=QueryIntent.TIME_COMPARISON,
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
        intent=QueryIntent.TIME_COMPARISON,
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
        intent=QueryIntent.TIME_COMPARISON,
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
        intent=QueryIntent.TIME_COMPARISON,
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
        intent=QueryIntent.TIME_COMPARISON,
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
        intent=QueryIntent.TRANSACTION_DETAIL,
        filters=QueryFilters(recipient="Mum", transaction_type="debit"),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="when did I last pay Mum this month",
        request_shape=QueryRequestShape.FACT,
        fact_query_kind=FactQueryKind.DATE,
        result_limit=1,
        result_reference="latest",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.filters is not None
    assert query_ir.filters.counterparty == ["Mum"]
    assert query_ir.filters.merchant is None
    assert contract.answer_fact_field == "date"






def test_request_shape_fact_overrides_grouped_summary_without_keyword_recovery() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 28)
    extraction = QueryExtractionResult(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        request_shape=QueryRequestShape.FACT,
        fact_query_kind=FactQueryKind.DATE,
        filters=QueryFilters(recipient="Mum"),
        time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED, days_back=30),
        raw_query="restated follow-up",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")

    assert query_ir.answer_fact_field == "date"


def test_who_sent_me_query_sets_counterparty_answer_fact() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_DETAIL,
        request_shape=QueryRequestShape.FACT,
        fact_query_kind=FactQueryKind.COUNTERPARTY,
        filters=QueryFilters(min_amount=500000, max_amount=500000),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
        raw_query="who sent me 500k last week",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")

    assert query_ir.filters is not None
    assert query_ir.filters.transaction_type == "credit"
    assert query_ir.answer_fact_field == "counterparty"


def test_who_sent_me_most_money_compiles_to_credit_beneficiary_summary() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        aggregation=QueryAggregation(type="sum", sort_by="amount"),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="who sent me the most money this month",
        result_limit=1,
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_ir.filters is not None
    assert query_ir.filters.transaction_type == "credit"
    assert query_ir.aggregation is not None
    assert query_ir.aggregation.sort_by == "amount"
    assert query_ir.result_limit == 1
    assert contract.filters is not None
    assert contract.filters.transaction_type == "credit"
    assert contract.result_limit == 1


def test_beneficiary_aggregation_limit_one_compiles_to_single_result_limit() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        aggregation=QueryAggregation(type="sum", sort_by="amount", limit=1),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="semantic singular beneficiary winner",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_ir.result_limit == 1
    assert contract.result_limit == 1


def test_explicit_top_senders_query_does_not_compile_to_single_result_limit() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        aggregation=QueryAggregation(type="sum", sort_by="amount"),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="show top senders this month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_ir.result_limit is None
    assert contract.result_limit is None




def test_counterparty_placeholder_is_ignored_for_sender_fact_queries() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_DETAIL,
        request_shape=QueryRequestShape.FACT,
        fact_query_kind=FactQueryKind.COUNTERPARTY,
        filters=QueryFilters(recipient="unknown", min_amount=500000, max_amount=500000),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
        raw_query="who sent me 500k last week",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")

    assert query_ir.filters is not None
    assert query_ir.filters.counterparty is None
    assert query_ir.filters.transaction_type == "credit"
    assert query_ir.answer_fact_field == "counterparty"


@pytest.mark.parametrize(
    ("raw_query", "intent"),
    [
        ("when did I last pay Mum", QueryIntent.TRANSACTION_DETAIL),
        ("which bank was that", QueryIntent.TRANSACTION_LIST),
    ],
)
def test_raw_text_alone_does_not_infer_fact_fields(raw_query: str, intent: QueryIntent) -> None:
    parser = QueryParser(_DummyLLM())
    extraction_kwargs: dict[str, object] = {}
    if "Mum" in raw_query:
        extraction_kwargs["filters"] = QueryFilters(recipient="Mum")
    extraction = QueryExtractionResult(
        intent=intent,
        time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
        raw_query=raw_query,
        result_limit=1,
        result_reference="latest",
        **extraction_kwargs,
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=date(2026, 3, 28), language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.answer_fact_field is None
    assert contract.answer_fact_field is None


@pytest.mark.parametrize(
    ("fact_query_kind", "expected"),
    [
        (FactQueryKind.DATE, "date"),
        (FactQueryKind.COUNTERPARTY, "counterparty"),
        (FactQueryKind.AMOUNT, "amount"),
        (FactQueryKind.BANK, "bank"),
        (FactQueryKind.REFERENCE, "reference"),
        (FactQueryKind.STATUS, "status"),
        (FactQueryKind.CATEGORY, "category"),
    ],
)
def test_typed_fact_query_kind_compiles_to_answer_fact_field(
    fact_query_kind: FactQueryKind,
    expected: str,
) -> None:
    parser = QueryParser(_DummyLLM())
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        request_shape=QueryRequestShape.FACT,
        fact_query_kind=fact_query_kind,
        time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
        raw_query="typed semantic fact query",
        result_reference="latest",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=date(2026, 3, 28), language="en")

    assert query_ir.answer_fact_field == expected




def test_existence_request_shape_compiles_to_direct_sum_query() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 28)
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        request_shape=QueryRequestShape.EXISTENCE,
        filters=QueryFilters(recipient="Mum", transaction_type="debit"),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="did I send money to Mum this month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")
    contract = parser.build_execution_contract_from_ir(query_ir)

    assert query_ir.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_ir.request_shape == "existence"
    assert query_ir.filters is not None
    assert query_ir.filters.counterparty == ["Mum"]
    assert query_ir.filters.transaction_type == "debit"
    assert contract.request_shape == "existence"




def test_spending_by_account_overrides_wrong_extracted_category_grouping() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 21)
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        aggregation=QueryAggregation(type="breakdown", group_by="category"),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="break down my spending by account",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")

    assert query_ir.aggregation is not None
    assert query_ir.aggregation.type == "breakdown"
    assert query_ir.aggregation.group_by == "account"
    assert query_ir.filters is not None
    assert query_ir.filters.transaction_type == "debit"


def test_amount_filtered_people_query_compiles_to_beneficiary_summary_with_rolling_window() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 27)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
        raw_query="show people I sent 20k to in the last 2 weeks",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")

    assert query_ir.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_ir.aggregation is not None
    assert query_ir.aggregation.sort_by == "amount"
    assert query_ir.filters is not None
    assert query_ir.filters.transaction_type == "debit"
    assert query_ir.filters.min_amount == 20000
    assert query_ir.filters.max_amount == 20000
    assert query_ir.time_range.start == date(2026, 3, 14)
    assert query_ir.time_range.end == today


def test_greater_than_amount_people_query_compiles_to_beneficiary_summary_with_min_filter() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 27)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
        raw_query="show people I sent greater than 20k to in the last 2 weeks",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")

    assert query_ir.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_ir.filters is not None
    assert query_ir.filters.transaction_type == "debit"
    assert query_ir.filters.min_amount == 20000
    assert query_ir.filters.max_amount is None
    assert query_ir.time_range.start == date(2026, 3, 14)
    assert query_ir.time_range.end == today


def test_plain_people_query_compiles_to_beneficiary_summary_without_literal_people_filter() -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 30)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        filters=QueryFilters(recipient="people"),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query="show people I sent money to this month",
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language="en")

    assert query_ir.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_ir.filters is not None
    assert query_ir.filters.transaction_type == "debit"
    assert query_ir.filters.counterparty is None
    assert query_ir.aggregation is not None
    assert query_ir.aggregation.sort_by == "amount"


@pytest.mark.parametrize(
    ("question", "language", "placeholder_recipient"),
    [
        ("who I send money give this month", "pcm", "pesin"),
        ("tani mo ran owo si ni osu yi", "yo", "eniyan"),
        ("onye ka m zigara ego n'onwa a", "ig", "nnata"),
        ("wa na tura wa kudi a wannan watan", "ha", "mutanen"),
        ("qui ai je envoye de l argent ce mois ci", "fr", "personnes"),
        ("tani mo send money to this month", "yo", "eniyan"),
    ],
)
def test_multilingual_recipient_summary_queries_compile_to_beneficiary_summary(
    question: str,
    language: str,
    placeholder_recipient: str,
) -> None:
    parser = QueryParser(_DummyLLM())
    today = date(2026, 3, 30)
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        filters=QueryFilters(recipient=placeholder_recipient),
        time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
        raw_query=question,
    )

    query_ir = parser.build_query_ir_from_extraction(extraction, today=today, language=language)

    assert query_ir.intent == QueryIntent.BENEFICIARY_SUMMARY
    assert query_ir.filters is not None
    assert query_ir.filters.transaction_type == "debit"
    assert query_ir.filters.counterparty is None
    assert query_ir.aggregation is not None
    assert query_ir.aggregation.sort_by == "amount"
