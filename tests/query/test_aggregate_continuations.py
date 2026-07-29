from datetime import date
from typing import Any

import pytest

from banking.transactions.query.compiler.aggregation import (
    build_default_aggregation,
    infer_breakdown_group_by,
)
from banking.transactions.query.continuations.aggregate_continuations import (
    _sanitize_aggregate_extraction,
    compile_aggregate_continuation_updates,
)
from banking.transactions.query.continuations.scope_rescope import drop_narrow_scope_filters
from banking.transactions.query.models.domain import (
    Filters,
    QueryIntent,
    QueryRequest,
)
from banking.transactions.query.models.extraction import (
    FactQueryKind,
    QueryAggregation,
    QueryExtractionResult,
    QueryRequestShape,
)
from banking.transactions.query.models.operations import (
    AllAccounts,
    GroupedSummarySpec,
    QueryScope,
    ResolvedPeriod,
    RetrieveOperation,
    ScalarSummarySpec,
    SummarizeOperation,
    TransactionPredicate,
)
from banking.transactions.query.nodes.extraction import ExtractionStep
from banking.transactions.shared.correction_markers import is_scope_broadening_correction


class _DummyLLM:
    def with_structured_output(self, schema: object) -> Any:
        del schema
        return None


def _food_transaction_list_request() -> QueryRequest:
    return QueryRequest(
        operation=RetrieveOperation(
            scope=QueryScope(
                period=ResolvedPeriod(start=date(2026, 6, 30), end=date(2026, 7, 29)),
                accounts=AllAccounts(),
                predicate=TransactionPredicate(direction="debit", categories=["food"]),
            ),
            projection={},
            selection={},
        ),
    )


def test_sanitize_aggregate_extraction_clears_fact_and_limit_fields() -> None:
    extraction = QueryExtractionResult(
        answer_fact_field="date",
        fact_query_kind=FactQueryKind.DATE,
        request_shape=QueryRequestShape.FACT,
        result_limit=1,
        result_reference="latest",
    )

    sanitized = _sanitize_aggregate_extraction(extraction)

    assert sanitized.answer_fact_field is None
    assert sanitized.fact_query_kind is None
    assert sanitized.request_shape == QueryRequestShape.ANALYTICS
    assert sanitized.result_limit is None
    assert sanitized.result_reference is None


def test_infer_breakdown_group_by_detects_bank_phrase() -> None:
    extraction = QueryExtractionResult(raw_query="Show my spending by banks")
    assert infer_breakdown_group_by(extraction) == "account"


def test_build_default_aggregation_returns_breakdown_for_by_bank() -> None:
    extraction = QueryExtractionResult(raw_query="Show my spending by banks")
    agg = build_default_aggregation(extraction, intent=QueryIntent.ANALYTICS_SUMMARY)
    assert agg is not None
    assert agg.type == "breakdown"
    assert agg.group_by == "account"


def test_scope_broadening_correction_detects_whole_spending() -> None:
    assert is_scope_broadening_correction("I mean my whole spending now")
    assert is_scope_broadening_correction("I meant all my spending")
    assert not is_scope_broadening_correction("Show my spending by banks")
    assert not is_scope_broadening_correction("I mean food")


def test_drop_narrow_scope_filters_preserves_direction_amount_status() -> None:
    filters = Filters(
        transaction_type="debit",
        category=["food"],
        merchant=["Glovo"],
        min_amount=1000.0,
        status="successful",
    )
    broadened = drop_narrow_scope_filters(filters)
    assert broadened.transaction_type == "debit"
    assert broadened.min_amount == 1000.0
    assert broadened.status == "successful"
    assert broadened.category is None
    assert broadened.merchant is None


@pytest.mark.asyncio
async def test_aggregate_continuation_switches_dimension_and_drops_old_filter() -> None:
    step = ExtractionStep(_DummyLLM())
    step.parser.parse_deterministic = lambda *args, **kwargs: None  # type: ignore[method-assign]

    decision = QueryExtractionResult(
        raw_query="Show my spending by banks",
        intent=QueryIntent.ANALYTICS_SUMMARY,
        aggregation=QueryAggregation(type="sum"),
    )

    async def _parse_reasoner_extraction_to_updates(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {}

    async def _parse_result_to_updates(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {}

    result = await compile_aggregate_continuation_updates(
        step,
        decision=decision,
        state={"message": "Show my spending by banks"},
        today=date(2026, 7, 29),
        language="en",
        session_query_request=_food_transaction_list_request(),
        parse_result_to_updates=_parse_result_to_updates,
        parse_reasoner_extraction_to_updates=_parse_reasoner_extraction_to_updates,
    )

    assert result is not None
    query_request = result["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert isinstance(query_request.operation, SummarizeOperation)
    assert isinstance(query_request.operation.summary, GroupedSummarySpec)
    assert query_request.operation.summary.dimension == "account"
    assert query_request.operation.summary.statistic == "sum"
    assert query_request.filters is None or query_request.filters.category is None
    assert query_request.filters.transaction_type == "debit"


@pytest.mark.asyncio
async def test_scope_broadening_recovery_drops_food_filter() -> None:
    from banking.transactions.query.continuations.scope_rescope import (
        maybe_recover_scope_broadening_continuation,
    )

    result = await maybe_recover_scope_broadening_continuation(
        message="I mean my whole spending now",
        session_query_request=_food_transaction_list_request(),
    )

    assert result is not None
    query_request = result["query_request"]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert isinstance(query_request.operation.summary, ScalarSummarySpec)
    assert query_request.operation.summary.statistic == "sum"
    assert query_request.filters is None or query_request.filters.category is None
    assert query_request.filters.transaction_type == "debit"
