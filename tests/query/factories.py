"""Native query-operation factories shared by behavioral tests."""

from __future__ import annotations

from datetime import date
from typing import Any

from banking.transactions.query.models.conversation import PendingFieldClarification, QueryInputCandidate
from banking.transactions.query.models.domain import Aggregation, Filters, QueryIntent, TimeRange
from banking.transactions.query.models.extraction import (
    Ambiguity,
    ClarificationCandidate,
    ClarificationOperation,
    QueryExtractionResult,
)
from banking.transactions.query.models.operations import (
    AccountSelector,
    AffordabilitySpec,
    AllAccounts,
    AmountRange,
    AnalyzeOperation,
    AssessOperation,
    CashFlowSummarySpec,
    CompareOperation,
    ComparisonBaseline,
    CounterpartySelector,
    GroupedSummarySpec,
    Money,
    NamedAccount,
    NamedCounterparty,
    PeriodComparisonSpec,
    PreviousEquivalentBaseline,
    QueryRequest,
    QueryScope,
    ResolvedPeriod,
    RetrieveOperation,
    RetrieveProjection,
    RetrieveSelection,
    ScalarSummarySpec,
    SummarizeOperation,
    SummarySpec,
    TextMatch,
    TransactionPredicate,
    VarianceDriversSpec,
)


def query_scope(
    start: date,
    end: date,
    *,
    predicate: TransactionPredicate | None = None,
    accounts: AccountSelector | None = None,
    granularity: str | None = None,
) -> QueryScope:
    return QueryScope(
        period=ResolvedPeriod(start=start, end=end, granularity=granularity),  # type: ignore[arg-type]
        predicate=predicate or TransactionPredicate(),
        accounts=accounts or AllAccounts(),
    )


def retrieve_request(
    scope: QueryScope,
    *,
    projection: RetrieveProjection | None = None,
    selection: RetrieveSelection | None = None,
) -> QueryRequest:
    return QueryRequest(
        operation=RetrieveOperation(
            scope=scope,
            projection=projection or RetrieveProjection(),
            selection=selection or RetrieveSelection(),
        )
    )


def summarize_request(scope: QueryScope, summary: SummarySpec) -> QueryRequest:
    return QueryRequest(operation=SummarizeOperation(scope=scope, summary=summary))


def compare_request(
    scope: QueryScope,
    *,
    baseline: ComparisonBaseline | None = None,
) -> QueryRequest:
    return QueryRequest(
        operation=CompareOperation(
            scope=scope,
            comparison=PeriodComparisonSpec(
                baseline=baseline or PreviousEquivalentBaseline(),
                measures=["spending", "income", "net_cash_flow", "transactions"],
            ),
        )
    )


def analyze_request(scope: QueryScope, analysis: VarianceDriversSpec | None = None) -> QueryRequest:
    return QueryRequest(operation=AnalyzeOperation(scope=scope, analysis=analysis or VarianceDriversSpec()))


def assess_request(amount: float, *, item_name: str | None = None) -> QueryRequest:
    return QueryRequest(
        operation=AssessOperation(assessment=AffordabilitySpec(amount=Money(amount=amount), item_name=item_name))
    )


def make_query_request(
    *,
    intent: QueryIntent = QueryIntent.TRANSACTION_LIST,
    time_range: TimeRange | None = None,
    time_start: date | None = None,
    time_end: date | None = None,
    filters: Filters | None = None,
    aggregation: Aggregation | None = None,
    accounts_scope: str = "all",
    account_name: str | None = None,
    amount_check: float | None = None,
    item_name: str | None = None,
    result_limit: int | None = None,
    result_reference: str | None = None,
    answer_fact_field: str | None = None,
    request_shape: str | None = None,
    **_: object,
) -> QueryRequest:
    """Build a genuine discriminated operation for broad behavior matrices."""
    if isinstance(filters, dict):
        filters = Filters.model_validate(filters)
    if isinstance(aggregation, dict):
        aggregation = Aggregation.model_validate(aggregation)
    request_shape = getattr(request_shape, "value", request_shape)
    today = date.today()
    start = time_start or (time_range.start if time_range else today)
    end = time_end or (time_range.end if time_range else today)
    accounts = NamedAccount(name=account_name) if accounts_scope == "single" and account_name else AllAccounts()
    predicate = TransactionPredicate()
    if filters is not None:
        predicate = TransactionPredicate(
            amount=AmountRange(
                minimum=Money(amount=filters.min_amount) if filters.min_amount is not None else None,
                maximum=Money(amount=filters.max_amount) if filters.max_amount is not None else None,
                minimum_inclusive=filters.min_amount_inclusive,
                maximum_inclusive=filters.max_amount_inclusive,
            )
            if filters.min_amount is not None or filters.max_amount is not None
            else None,
            direction=filters.transaction_type,
            statuses=[filters.status] if filters.status else [],
            categories=list(filters.category or []),
            counterparty=CounterpartySelector(role="any", reference=NamedCounterparty(name=filters.counterparty[0]))
            if filters.counterparty
            else None,
            narration=TextMatch(query=filters.merchant[0]) if filters.merchant else None,
            exclusions=list(filters.exclude or []),
        )
        if filters.account_filter:
            accounts = NamedAccount(name=filters.account_filter)
    scope = query_scope(
        start,
        end,
        predicate=predicate,
        accounts=accounts,
        granularity=time_range.granularity if time_range else None,
    )
    if request_shape == "existence":
        return retrieve_request(
            scope,
            projection=RetrieveProjection(shape="existence"),
            selection=RetrieveSelection(cardinality="existence", limit=1),
        )
    if intent == QueryIntent.AFFORDABILITY:
        return assess_request(amount_check or 1, item_name=item_name)
    if intent == QueryIntent.TIME_COMPARISON:
        return compare_request(scope)
    if intent == QueryIntent.INSIGHT:
        return analyze_request(scope)
    if intent == QueryIntent.CASH_FLOW_SUMMARY:
        group_by = "account" if aggregation and aggregation.group_by == "account" else None
        return summarize_request(scope, CashFlowSummarySpec(group_by=group_by))
    if intent in {QueryIntent.ANALYTICS_SUMMARY, QueryIntent.BENEFICIARY_SUMMARY}:
        measure = (
            "income"
            if predicate.direction == "credit"
            else "spending"
            if predicate.direction == "debit"
            else "transactions"
        )
        if intent == QueryIntent.BENEFICIARY_SUMMARY or (aggregation and aggregation.group_by):
            group_by = aggregation.group_by if aggregation else None
            dimension = (
                "counterparty"
                if intent == QueryIntent.BENEFICIARY_SUMMARY or group_by == "merchant"
                else group_by or "category"
            )
            return summarize_request(
                scope,
                GroupedSummarySpec(
                    measure=measure,
                    statistic="count" if aggregation and aggregation.type == "count" else "sum",
                    dimension=dimension,  # type: ignore[arg-type]
                    rank_by=(aggregation.sort_by or "amount") if aggregation else "amount",
                    limit=aggregation.limit if aggregation else 5,
                    answer_cardinality="one" if result_limit == 1 else "many",
                ),
            )
        statistic = aggregation.type if aggregation and aggregation.type != "breakdown" else "sum"
        return summarize_request(
            scope,
            ScalarSummarySpec(measure=measure, statistic=statistic),  # type: ignore[arg-type]
        )
    shape = "fact" if answer_fact_field else "detail" if intent == QueryIntent.TRANSACTION_DETAIL else "list"
    return retrieve_request(
        scope,
        projection=RetrieveProjection(shape=shape, fact_field=answer_fact_field),  # type: ignore[arg-type]
        selection=RetrieveSelection(
            cardinality="existence"
            if shape == "existence"
            else "one"
            if shape in {"fact", "detail"} or result_limit == 1
            else "many",
            order=result_reference or "latest",  # type: ignore[arg-type]
            order_explicit=result_reference is not None,
            limit=result_limit,
        ),
    )


def make_pending_input(
    *,
    original_query: str,
    original_extraction: QueryExtractionResult | None = None,
    ambiguities: list[Ambiguity] | None = None,
    resolver_message: str | None = None,
    language: str = "en",
    clarification_type: str | None = None,
    target_field: str | None = None,
    candidate_payloads: list[ClarificationCandidate] | None = None,
    original_operation: ClarificationOperation | None = None,
    query_request: QueryRequest | dict[str, Any] | None = None,
    attempt_count: int = 0,
    created_turn_id: str | None = None,
) -> PendingFieldClarification:
    """Build the canonical query pending-input contract for behavioral tests."""
    request = (
        query_request
        if isinstance(query_request, QueryRequest)
        else QueryRequest.model_validate(query_request)
        if isinstance(query_request, dict)
        else None
    )
    return PendingFieldClarification(
        original_query=original_query,
        original_extraction=original_extraction.model_dump(mode="json") if original_extraction else None,
        ambiguities=[item.model_dump(mode="json") for item in ambiguities or []],
        resolver_message=resolver_message,
        language=language,
        clarification_type=clarification_type,  # type: ignore[arg-type]
        target_field=target_field,
        candidate_payloads=[
            QueryInputCandidate(label=item.label, payload=item.payload, frame_id=item.frame_id)
            for item in candidate_payloads or []
        ],
        original_operation=original_operation.model_dump(mode="json") if original_operation else {},
        query_request=request,
        attempt_count=attempt_count,
        created_turn_id=created_turn_id,
    )
