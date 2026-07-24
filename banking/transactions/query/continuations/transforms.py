"""Immutable transformations over authoritative query operations."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal, cast

from banking.transactions.query.models.domain import Aggregation, Filters, QueryIntent, TimeRange
from banking.transactions.query.models.operations import (
    AmountConstraint,
    AmountRange,
    CashFlowSummarySpec,
    CompareOperation,
    ExactAmount,
    GroupedSummarySpec,
    Money,
    NamedAccount,
    NamedCounterparty,
    PeriodComparisonSpec,
    PreviousEquivalentBaseline,
    QueryFactField,
    QueryOperation,
    QueryRequest,
    ResolvedPeriod,
    RetrieveOperation,
    RetrieveProjection,
    RetrieveSelection,
    ScalarSummarySpec,
    SummarizeOperation,
    SummarySpec,
    TextMatch,
    TransactionPredicate,
)

_UNCHANGED = object()


def _predicate(filters: Filters | None, current: TransactionPredicate) -> TransactionPredicate:
    if filters is None:
        return TransactionPredicate()
    amount: AmountConstraint | None = None
    if filters.min_amount is not None and filters.max_amount is not None and filters.min_amount == filters.max_amount:
        amount = ExactAmount(value=Money(amount=Decimal(str(filters.min_amount))))
    elif filters.min_amount is not None or filters.max_amount is not None:
        amount = AmountRange(
            minimum=Money(amount=Decimal(str(filters.min_amount))) if filters.min_amount is not None else None,
            maximum=Money(amount=Decimal(str(filters.max_amount))) if filters.max_amount is not None else None,
            minimum_inclusive=filters.min_amount_inclusive,
            maximum_inclusive=filters.max_amount_inclusive,
        )
    counterparty = current.counterparty
    if filters.counterparty:
        from banking.transactions.query.models.operations import CounterpartySelector

        counterparty = CounterpartySelector(role="any", reference=NamedCounterparty(name=filters.counterparty[0]))
    return TransactionPredicate(
        amount=amount,
        direction=filters.transaction_type,
        statuses=[filters.status] if filters.status else [],
        categories=filters.category or [],
        counterparty=counterparty,
        narration=TextMatch(query=filters.merchant[0]) if filters.merchant else None,
        exclusions=filters.exclude or [],
    )


def rebuild_query_request(
    original_contract: QueryRequest,
    *,
    filters: Filters | object = _UNCHANGED,
    merge_filters: bool = False,
    time_range: TimeRange | object = _UNCHANGED,
    intent: Any = _UNCHANGED,
    aggregation: Any = _UNCHANGED,
    result_limit: int | None | object = _UNCHANGED,
    result_reference: str | None | object = _UNCHANGED,
    answer_fact_field: str | None | object = _UNCHANGED,
    comparison: Any = _UNCHANGED,
    **_: Any,
) -> QueryRequest:
    """Return a new request by updating nested operation semantics."""
    scope = original_contract.scope
    if scope is None:
        return original_contract.model_copy(deep=True)
    if isinstance(time_range, (TimeRange, ResolvedPeriod)):
        scope = scope.model_copy(
            update={
                "period": ResolvedPeriod(start=time_range.start, end=time_range.end, granularity=time_range.granularity)
            }
        )
    if filters is not _UNCHANGED:
        next_filters = filters if isinstance(filters, Filters) else None
        if merge_filters and next_filters is not None:
            merged = original_contract.filters.model_dump(exclude_none=True) if original_contract.filters else {}
            merged.update(next_filters.model_dump(exclude_none=True))
            next_filters = Filters.model_validate(merged)
        scope = scope.model_copy(update={"predicate": _predicate(next_filters, scope.predicate)})
        if next_filters is not None and next_filters.account_filter:
            scope = scope.model_copy(update={"accounts": NamedAccount(name=next_filters.account_filter)})

    target_intent = original_contract.intent if intent is _UNCHANGED else intent
    if target_intent in {QueryIntent.TRANSACTION_LIST, QueryIntent.TRANSACTION_SEARCH, QueryIntent.TRANSACTION_DETAIL}:
        current_retrieve = (
            original_contract.operation if isinstance(original_contract.operation, RetrieveOperation) else None
        )
        fact_field = (
            answer_fact_field
            if isinstance(answer_fact_field, str)
            else current_retrieve.projection.fact_field
            if answer_fact_field is _UNCHANGED and current_retrieve is not None
            else None
        )
        limit = (
            result_limit
            if isinstance(result_limit, int)
            else current_retrieve.selection.limit
            if result_limit is _UNCHANGED and current_retrieve is not None
            else None
        )
        order: Literal["latest", "oldest", "largest", "smallest"] = "latest"
        if isinstance(result_reference, str) and result_reference in {"latest", "oldest"}:
            order = cast(Literal["latest", "oldest"], result_reference)
        elif result_reference is _UNCHANGED and current_retrieve is not None:
            order = current_retrieve.selection.order
        order_explicit = (
            True
            if result_reference in {"latest", "oldest"}
            else current_retrieve.selection.order_explicit
            if result_reference is _UNCHANGED and current_retrieve is not None
            else False
        )
        projection = RetrieveProjection(
            shape="fact" if fact_field else "list",
            fact_field=cast(QueryFactField | None, fact_field),
        )
        selection = RetrieveSelection(
            cardinality="one" if fact_field or limit == 1 else "many",
            limit=limit,
            order=order,
            order_explicit=order_explicit,
        )
        operation: QueryOperation = RetrieveOperation(scope=scope, projection=projection, selection=selection)
    elif target_intent == QueryIntent.TIME_COMPARISON:
        operation = CompareOperation(
            scope=scope,
            comparison=PeriodComparisonSpec(baseline=PreviousEquivalentBaseline(), measures=["transactions"]),
        )
    elif target_intent == QueryIntent.CASH_FLOW_SUMMARY:
        operation = SummarizeOperation(scope=scope, summary=CashFlowSummarySpec())
    elif target_intent in {QueryIntent.ANALYTICS_SUMMARY, QueryIntent.BENEFICIARY_SUMMARY}:
        agg = aggregation if isinstance(aggregation, Aggregation) else original_contract.aggregation or Aggregation()
        if target_intent == QueryIntent.BENEFICIARY_SUMMARY or agg.group_by:
            dimension = (
                "counterparty"
                if target_intent == QueryIntent.BENEFICIARY_SUMMARY or agg.group_by == "merchant"
                else agg.group_by
            )
            summary: SummarySpec = GroupedSummarySpec(
                measure="transactions",
                statistic="count" if agg.type == "count" else "sum",
                dimension=cast(
                    Literal["category", "counterparty", "day", "account", "transaction_type"],
                    dimension or "category",
                ),
                limit=agg.limit,
            )
        else:
            summary = ScalarSummarySpec(
                measure="transactions",
                statistic=cast(
                    Literal["sum", "count", "average", "largest", "smallest"],
                    agg.type if agg.type != "breakdown" else "sum",
                ),
            )
        operation = SummarizeOperation(scope=scope, summary=summary)
    else:
        operation = original_contract.operation.model_copy(update={"scope": scope})
    return original_contract.model_copy(update={"operation": operation})
