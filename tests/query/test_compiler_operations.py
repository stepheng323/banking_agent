from banking.transactions.query.compiler.operations import normalize_query_extraction
from banking.transactions.query.models.domain import QueryIntent
from banking.transactions.query.models.extraction import (
    QueryAggregation,
    QueryExtractionResult,
    QueryFilters,
)


def test_affordability_query_without_amount_forces_clarification() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.AFFORDABILITY,
        filters=QueryFilters(min_amount=None, max_amount=None),
        raw_query="can I afford this?",
    )

    result = normalize_query_extraction(extraction)

    assert result.intent == QueryIntent.QUERY_CLARIFICATION


def test_affordability_query_with_amount_keeps_intent() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.AFFORDABILITY,
        filters=QueryFilters(min_amount=5000),
        raw_query="can I afford 5000?",
    )

    result = normalize_query_extraction(extraction)

    assert result.intent == QueryIntent.AFFORDABILITY


def test_can_i_send_amount_normalizes_to_affordability() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        raw_query="Can I send 100k?",
    )

    result = normalize_query_extraction(extraction)

    assert result.intent == QueryIntent.AFFORDABILITY
    assert result.filters is not None
    assert result.filters.min_amount == 100000
    assert result.filters.max_amount == 100000


def test_transaction_detail_without_identifying_filters_forces_clarification() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_DETAIL,
        filters=QueryFilters(),  # Empty filters
        raw_query="tell me about that transaction",
    )

    result = normalize_query_extraction(extraction)

    assert result.intent == QueryIntent.QUERY_CLARIFICATION


def test_transaction_detail_with_identifying_filters_keeps_intent() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_DETAIL,
        filters=QueryFilters(narration_keyword="uber"),
        raw_query="tell me about that uber transaction",
    )

    result = normalize_query_extraction(extraction)

    assert result.intent == QueryIntent.TRANSACTION_DETAIL


def test_bidirectional_money_question_normalizes_to_cash_flow() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        raw_query="Did I spend more than I earned this month?",
    )

    result = normalize_query_extraction(extraction)

    assert result.intent == QueryIntent.CASH_FLOW_SUMMARY


def test_single_direction_inflow_total_overrides_wrong_cashflow_intent() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.CASH_FLOW_SUMMARY,
        raw_query="How much came in this month",
    )

    result = normalize_query_extraction(extraction)

    assert result.intent == QueryIntent.ANALYTICS_SUMMARY
    assert result.filters.transaction_type == "credit"
    assert result.aggregation is not None
    assert result.aggregation.type == "sum"


def test_cashflow_word_normalizes_to_cash_flow() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.TRANSACTION_LIST,
        raw_query="cashflow this month",
    )

    result = normalize_query_extraction(extraction)

    assert result.intent == QueryIntent.CASH_FLOW_SUMMARY


def test_income_vs_spending_grouping_normalizes_to_cash_flow() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        aggregation=QueryAggregation(type="sum", group_by="transaction_type"),
        raw_query="income vs spending this month",
    )

    result = normalize_query_extraction(extraction)

    assert result.intent == QueryIntent.CASH_FLOW_SUMMARY


def test_show_spending_repairs_hallucinated_smallest_aggregation_to_a_debit_list() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        aggregation=QueryAggregation(type="smallest", limit=1),
        raw_query="never mind, show my spending this month",
    )

    result = normalize_query_extraction(extraction)

    assert result.intent == QueryIntent.TRANSACTION_LIST
    assert result.request_shape is not None
    assert result.request_shape.value == "list"
    assert result.aggregation is None
    assert result.filters.transaction_type == "debit"


def test_show_smallest_expense_preserves_explicit_extrema() -> None:
    extraction = QueryExtractionResult(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        aggregation=QueryAggregation(type="smallest", limit=1),
        raw_query="show my smallest expense this month",
    )

    result = normalize_query_extraction(extraction)

    assert result.intent == QueryIntent.ANALYTICS_SUMMARY
    assert result.aggregation is not None
    assert result.aggregation.type == "smallest"
