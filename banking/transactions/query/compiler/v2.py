"""Direct compiler from parser extraction to Query Semantics v2."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, cast

from banking.transactions.query.compiler import query_compiler, time_ranges
from banking.transactions.query.compiler.operations import normalize_query_extraction
from banking.transactions.query.models.domain import Aggregation, Filters, QueryIntent, TimeRange
from banking.transactions.query.models.extraction import QueryExtractionResult, InsightSpec as ExtractionInsightSpec
from banking.transactions.query.models.operations import (
    AccountSelector,
    AffordabilitySpec,
    AllAccounts,
    AmountConstraint,
    AmountRange,
    AnalyzeOperation,
    ApproximateAmount,
    AssessOperation,
    CashFlowSummarySpec,
    CompareOperation,
    CounterpartySelector,
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
    UnspecifiedCounterparty,
    VarianceDriversSpec,
    InsightSpec as OperationInsightSpec,
)

_COUNTERPARTY_PLACEHOLDERS = {"someone", "somebody", "person", "recipient", "sender", "merchant", "unknown"}
_APPROXIMATE_AMOUNT_TOKENS = ("about", "around", "approximately", "roughly", "close to")


def compile_query_request(
    parser: object,
    extraction: QueryExtractionResult,
    *,
    today: date,
    language: str = "en",
) -> QueryRequest:
    """Compile extraction directly into the sole executable query request."""

    extraction = normalize_query_extraction(extraction.model_copy(deep=True))
    compiled = query_compiler.compile_query_fields_from_extraction(
        parser,
        extraction,
        today=today,
        language=language,
    )
    compiled_intent = cast(QueryIntent, compiled["intent"])
    # Legacy normalization downgrades unsupported insights to transaction
    # retrieval.  V2 must preserve the requested analytical family so the
    # capability is rejected explicitly instead of executing a different query.
    intent = extraction.intent if extraction.intent == QueryIntent.INSIGHT else compiled_intent
    period = _resolved_period(cast(TimeRange, compiled["time_range"]))
    filters = cast(Filters | None, compiled["filters"])
    scope = QueryScope(
        period=period,
        accounts=_account_selector(compiled),
        predicate=_predicate(extraction, filters),
    )
    operation = _operation(
        intent=intent,
        scope=scope,
        extraction=extraction,
        aggregation=cast(Aggregation | None, compiled["aggregation"]),
        result_limit=cast(int | None, compiled["result_limit"]),
        result_reference=cast(str | None, compiled["result_reference"]),
        answer_fact_field=cast(str | None, compiled["answer_fact_field"]),
        amount_check=cast(float | None, compiled["amount_check"]),
        today=today,
    )
    return QueryRequest(operation=operation)


def _resolved_period(value: TimeRange) -> ResolvedPeriod:
    return ResolvedPeriod(start=value.start, end=value.end, granularity=value.granularity)


def _account_selector(compiled: dict[str, object]) -> AccountSelector:
    """Compile an explicit bank filter into the executable account scope.

    ``Filters.account_filter`` remains useful to presentation and legacy fetch
    paths, but the V2 operation contract owns the actual account selection.
    Without this conversion, a parser can correctly extract ``GTBank`` while
    the executor still fetches every linked account.
    """
    filters = compiled.get("filters")
    account_filter = getattr(filters, "account_filter", None)
    if isinstance(account_filter, str) and account_filter.strip():
        return NamedAccount(name=account_filter.strip())
    if compiled.get("accounts_scope") == "single" and compiled.get("account_name"):
        return NamedAccount(name=str(compiled["account_name"]))
    return AllAccounts()


def _money(value: float | int | Decimal) -> Money:
    return Money(amount=Decimal(str(value)))


def _amount_constraint(extraction: QueryExtractionResult, filters: Filters | None) -> AmountConstraint | None:
    if filters is None:
        return None
    minimum = filters.min_amount
    maximum = filters.max_amount
    if minimum is not None and maximum is not None and Decimal(str(minimum)) == Decimal(str(maximum)):
        exact = _money(minimum)
        raw = (extraction.raw_query or "").casefold()
        if any(token in raw for token in _APPROXIMATE_AMOUNT_TOKENS):
            return ApproximateAmount(value=exact)
        return ExactAmount(value=exact)
    if minimum is None and maximum is None:
        return None
    return AmountRange(
        minimum=_money(minimum) if minimum is not None else None,
        maximum=_money(maximum) if maximum is not None else None,
        minimum_inclusive=filters.min_amount_inclusive,
        maximum_inclusive=filters.max_amount_inclusive,
    )


def _counterparty(extraction: QueryExtractionResult, filters: Filters | None) -> CounterpartySelector | None:
    raw_recipient = " ".join((extraction.filters.recipient or "").strip().split())
    if raw_recipient.casefold() in _COUNTERPARTY_PLACEHOLDERS:
        role: Literal["sender", "recipient"] = (
            "sender" if filters and filters.transaction_type == "credit" else "recipient"
        )
        entity_type: Literal["merchant", "person"] = "merchant" if raw_recipient.casefold() == "merchant" else "person"
        return CounterpartySelector(
            role=role,
            reference=UnspecifiedCounterparty(entity_type=entity_type),
        )
    if filters and filters.counterparty:
        name = filters.counterparty[0]
        role = cast(Literal["sender", "recipient"], "sender" if filters.transaction_type == "credit" else "recipient")
        return CounterpartySelector(role=role, reference=NamedCounterparty(name=name))
    return None


def _predicate(extraction: QueryExtractionResult, filters: Filters | None) -> TransactionPredicate:
    raw = (extraction.raw_query or "").casefold()
    event_types: list[str] = []
    if any(token in raw for token in ("paid", "pay ", "sent", "send", "transfer")):
        event_types = ["transfer", "payment"]
    return TransactionPredicate(
        amount=_amount_constraint(extraction, filters),
        direction=filters.transaction_type if filters else None,
        statuses=[filters.status] if filters and filters.status else [],
        categories=list(filters.category or []) if filters else [],
        event_types=event_types,
        counterparty=_counterparty(extraction, filters),
        narration=TextMatch(query=filters.merchant[0]) if filters and filters.merchant else None,
        exclusions=list(filters.exclude or []) if filters else [],
    )


def _measure(scope: QueryScope) -> Literal["spending", "income", "transactions"]:
    if scope.predicate.direction == "credit":
        return "income"
    if scope.predicate.direction == "debit":
        return "spending"
    return "transactions"


def _summary(aggregation: Aggregation | None, scope: QueryScope) -> SummarySpec:
    if aggregation is None:
        return ScalarSummarySpec(measure=_measure(scope), statistic="sum")
    if aggregation.group_by:
        dimension = "counterparty" if aggregation.group_by == "merchant" else aggregation.group_by
        return GroupedSummarySpec(
            measure=_measure(scope),
            statistic="count" if aggregation.type == "count" else "sum",
            dimension=cast(
                Literal["category", "counterparty", "day", "account", "transaction_type"],
                dimension,
            ),
            rank_by=aggregation.sort_by or "amount",
            limit=aggregation.limit,
            answer_cardinality="one" if aggregation.limit == 1 else "many",
        )
    statistic = aggregation.type if aggregation.type != "breakdown" else "sum"
    return ScalarSummarySpec(
        measure=_measure(scope),
        statistic=cast(Literal["sum", "count", "average", "largest", "smallest"], statistic),
    )


def _operation(
    *,
    intent: QueryIntent,
    scope: QueryScope,
    extraction: QueryExtractionResult,
    aggregation: Aggregation | None,
    result_limit: int | None,
    result_reference: str | None,
    answer_fact_field: str | None,
    amount_check: float | None,
    today: date,
) -> QueryOperation:
    # An affordability intent is a read-only assessment. Some legacy parser
    # paths retain a generic fact shape on the extraction, so intent must take
    # precedence over response shape here.
    if intent == QueryIntent.AFFORDABILITY:
        if amount_check is None:
            raise ValueError("affordability assessment requires an amount")
        return AssessOperation(assessment=AffordabilitySpec(amount=_money(amount_check), accounts=scope.accounts))
    if extraction.request_shape is not None and extraction.request_shape.value == "existence":
        return RetrieveOperation(
            scope=scope,
            projection=RetrieveProjection(shape="existence"),
            selection=RetrieveSelection(cardinality="existence", order="latest", limit=1),
        )
    if answer_fact_field is not None or (
        extraction.request_shape is not None and extraction.request_shape.value in {"fact", "detail"}
    ):
        shape: Literal["fact", "detail"] = "fact" if answer_fact_field is not None else "detail"
        return RetrieveOperation(
            scope=scope,
            projection=RetrieveProjection(shape=shape, fact_field=cast(QueryFactField | None, answer_fact_field)),
            selection=RetrieveSelection(cardinality="one", order="latest", limit=result_limit or 1),
        )
    if intent in {QueryIntent.TRANSACTION_LIST, QueryIntent.TRANSACTION_SEARCH, QueryIntent.TRANSACTION_DETAIL}:
        projection_shape: Literal["fact", "detail", "list"] = (
            "fact" if answer_fact_field else "detail" if intent == QueryIntent.TRANSACTION_DETAIL else "list"
        )
        cardinality: Literal["one", "many"] = (
            "one" if projection_shape in {"fact", "detail"} or result_limit == 1 else "many"
        )
        return RetrieveOperation(
            scope=scope,
            projection=RetrieveProjection(
                shape=projection_shape,
                fact_field=cast(QueryFactField | None, answer_fact_field),
                include=["transaction", "counterparty"] if scope.predicate.counterparty else ["transaction"],
            ),
            selection=RetrieveSelection(
                cardinality=cardinality,
                order=cast(Literal["latest", "oldest", "largest", "smallest"], result_reference or "latest"),
                order_explicit=result_reference is not None,
                limit=result_limit,
            ),
        )
    if intent in {QueryIntent.ANALYTICS_SUMMARY, QueryIntent.BENEFICIARY_SUMMARY}:
        summary: SummarySpec
        if intent == QueryIntent.BENEFICIARY_SUMMARY:
            summary = GroupedSummarySpec(
                measure=_measure(scope),
                statistic="sum",
                dimension="counterparty",
                rank_by=aggregation.sort_by if aggregation and aggregation.sort_by else "amount",
                limit=aggregation.limit if aggregation else 5,
                answer_cardinality="one" if result_limit == 1 else "many",
            )
        else:
            summary = _summary(aggregation, scope)
        return SummarizeOperation(scope=scope, summary=summary)
    if intent == QueryIntent.CASH_FLOW_SUMMARY:
        return SummarizeOperation(
            scope=scope,
            summary=CashFlowSummarySpec(
                group_by="account" if aggregation and aggregation.group_by == "account" else None
            ),
        )
    if intent == QueryIntent.TIME_COMPARISON:
        baseline = time_ranges.build_comparison_baseline(
            extraction,
            intent=intent,
            current_range=TimeRange(
                start=scope.period.start,
                end=scope.period.end,
                granularity=scope.period.granularity,
            ),
            today=today,
        )
        measures = cast(
            list[Literal["spending", "income", "net_cash_flow", "transactions"]],
            ["spending", "income", "net_cash_flow"],
        )
        return CompareOperation(
            scope=scope,
            comparison=PeriodComparisonSpec(baseline=baseline or PreviousEquivalentBaseline(), measures=measures),
        )
    if intent == QueryIntent.INSIGHT:
        insight = extraction.insight
        if insight is None:
            raise ValueError("variance analysis requires insight parameters")
        return AnalyzeOperation(scope=scope, analysis=_insight_spec(insight, extraction, scope, today=today))
    raise ValueError(f"query intent is not executable: {intent.value}")


def _insight_spec(
    insight: ExtractionInsightSpec,
    extraction: QueryExtractionResult,
    scope: QueryScope,
    *,
    today: date,
) -> OperationInsightSpec:
    if insight.insight_type == "variance_drivers":
        baseline = time_ranges.build_comparison_baseline(
            extraction,
            intent=QueryIntent.TIME_COMPARISON,
            current_range=TimeRange(start=scope.period.start, end=scope.period.end, granularity=scope.period.granularity),
            today=today,
        )
        return VarianceDriversSpec(
            baseline=baseline or PreviousEquivalentBaseline(),
            analysis_basis=insight.analysis_basis,
            measure=insight.measure,
            dimensions=list(insight.dimensions),
            confidence_policy=insight.confidence_policy,
            evidence_limit=insight.evidence_limit,
            completeness_policy=insight.completeness_policy,
            evidence=insight.evidence,
        )
    
    import pydantic
    return pydantic.TypeAdapter(OperationInsightSpec).validate_python(insight.model_dump())
