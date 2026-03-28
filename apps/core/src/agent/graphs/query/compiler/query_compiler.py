"""QueryIR and runtime contract compilation helpers."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal, cast

from apps.core.src.agent.graphs.query.capabilities import QUERY_LIMITS
from apps.core.src.agent.graphs.query.compiler.lexical_recovery import (
    month_token,
    recover_known_fragile_query_shapes,
    resolve_month_period_with_year_hint,
)
from apps.core.src.agent.graphs.query.models import (
    Aggregation,
    ComparisonDirective,
    ExtractionIntent,
    FactQueryKind,
    Filters,
    QueryExecutionContract,
    QueryExtractionResult,
    QueryIntent,
    QueryIR,
    QueryOperation,
    QueryRequestShape,
    TimeRange,
    TimeReference,
    derive_query_intent_spec_from_fields,
)
from apps.core.src.agent.graphs.query.utils.timezone import lagos_today


def build_query_ir_from_extraction(
    parser: Any,
    extraction: QueryExtractionResult,
    *,
    today: date | None = None,
    language: str = "en",
    continuation_type: str | None = None,
    continuation_delta_type: str | None = None,
) -> QueryIR:
    base_today = today or lagos_today()
    compiled = parser._compile_query_fields_from_extraction(extraction, today=base_today)
    time_range = compiled["time_range"] or TimeRange(
        start=base_today - timedelta(days=30),
        end=base_today,
        granularity="day",
    )
    comparison = parser._build_comparison_directive(
        extraction,
        intent=cast(QueryIntent, compiled["intent"]),
        current_range=time_range,
        today=base_today,
    )

    return QueryIR(
        intent=cast(QueryIntent, compiled["intent"]),
        query_operation=cast(QueryOperation | None, compiled["query_operation"]),
        raw_query=extraction.raw_query,
        language=language,
        timezone="Africa/Lagos",
        time_range=time_range,
        filters=cast(Filters | None, compiled["filters"]),
        aggregation=cast(Aggregation | None, compiled["aggregation"]),
        accounts_scope=cast(Literal["single", "all"], compiled["accounts_scope"]),
        account_name=cast(str | None, compiled["account_name"]),
        amount_check=cast(float | None, compiled["amount_check"]),
        item_name=cast(str | None, compiled["item_name"]),
        analysis_type=cast(Literal["immediate", "relative", "simulated", "remainder"], compiled["analysis_type"]),
        result_limit=cast(int | None, compiled["result_limit"]),
        result_reference=cast(Literal["latest", "oldest"] | None, compiled["result_reference"]),
        answer_fact_field=cast(Literal["date", "counterparty", "amount", "bank"] | None, compiled["answer_fact_field"]),
        comparison=comparison,
        continuation_type=continuation_type,
        continuation_delta_type=continuation_delta_type,
        intent_spec=cast(Any, compiled["intent_spec"]),
    )


def resolve_period_to_range(period: str, *, today: date, current_range: TimeRange | None = None) -> TimeRange | None:
    def _duration_days(range_value: TimeRange | None) -> int:
        if range_value is None:
            return 0
        return max(1, (range_value.end - range_value.start).days + 1)

    token = period.strip().lower().replace("-", "_").replace(" ", "_")
    if token in {"today"}:
        return TimeRange(start=today, end=today, granularity="day")
    if token in {"yesterday"}:
        day = today - timedelta(days=1)
        return TimeRange(start=day, end=day, granularity="day")
    if token in {"this_week", "week", "current_week"}:
        week_start = today - timedelta(days=today.weekday())
        return TimeRange(start=week_start, end=today, granularity="week")
    if token in {"last_week", "previous_week"}:
        this_week_start = today - timedelta(days=today.weekday())
        week_end = this_week_start - timedelta(days=1)
        week_start = week_end - timedelta(days=6)
        duration = _duration_days(current_range)
        if duration <= 0:
            return TimeRange(start=week_start, end=week_end, granularity="week")
        aligned_end = min(week_end, week_start + timedelta(days=duration - 1))
        return TimeRange(start=week_start, end=aligned_end, granularity="week")
    if token in {"this_month", "current_month", "month"}:
        month_start = date(today.year, today.month, 1)
        return TimeRange(start=month_start, end=today, granularity="month")
    if token in {"last_month", "previous_month"}:
        year = today.year
        month = today.month - 1
        if month == 0:
            month = 12
            year -= 1
        last_day = __import__("calendar").monthrange(year, month)[1]
        month_start = date(year, month, 1)
        month_end = date(year, month, last_day)
        duration = _duration_days(current_range)
        if duration <= 0:
            return TimeRange(start=month_start, end=month_end, granularity="month")
        aligned_end = min(month_end, month_start + timedelta(days=duration - 1))
        return TimeRange(start=month_start, end=aligned_end, granularity="month")
    if token in {"this_year", "current_year", "year"}:
        return TimeRange(start=date(today.year, 1, 1), end=today, granularity="month")
    if token in {"last_year", "previous_year"}:
        year = today.year - 1
        return TimeRange(start=date(year, 1, 1), end=date(year, 12, 31), granularity="month")
    hinted_month_range = resolve_month_period_with_year_hint(period, today=today)
    if hinted_month_range is not None:
        return hinted_month_range
    month_number = month_token(token)
    if month_number is not None:
        year = today.year if month_number <= today.month else today.year - 1
        from calendar import monthrange

        month_start = date(year, month_number, 1)
        month_end = date(year, month_number, monthrange(year, month_number)[1])
        if year == today.year and month_number == today.month:
            month_end = today
        return TimeRange(start=month_start, end=month_end, granularity="month")
    return None


def build_comparison_directive(
    parser: Any,
    extraction: QueryExtractionResult,
    *,
    intent: QueryIntent,
    current_range: TimeRange,
    today: date,
) -> ComparisonDirective | None:
    if intent != QueryIntent.TIME_COMPARISON:
        return None
    comparison = extraction.comparison
    if comparison is None:
        return ComparisonDirective(mode="previous_equivalent")
    if comparison.mode == "year_ago":
        return ComparisonDirective(mode="year_ago")
    if comparison.mode == "explicit_period" and comparison.period:
        explicit_range = parser._resolve_period_to_range(comparison.period, today=today, current_range=current_range)
        if explicit_range is not None:
            return ComparisonDirective(mode="explicit_range", explicit_range=explicit_range)
        return ComparisonDirective(mode="previous_equivalent")
    return ComparisonDirective(mode="previous_equivalent")


def build_execution_contract_from_ir(query_ir: QueryIR) -> QueryExecutionContract:
    return QueryExecutionContract.from_query_ir(query_ir)


def compile_query_fields_from_extraction(parser: Any, extraction: QueryExtractionResult, *, today: date) -> dict[str, Any]:
    extraction = recover_known_fragile_query_shapes(extraction.model_copy(deep=True))
    effective_intent = parser._resolve_effective_intent(extraction)
    query_operation = parser._infer_query_operation(extraction, effective_intent=effective_intent)
    result_limit = parser._resolve_result_limit(extraction.result_limit, effective_intent=effective_intent)
    time_range = parser._build_time_range(extraction, today=today)
    filters = parser._build_filters(extraction, effective_intent=effective_intent, query_operation=query_operation)
    aggregation = parser._build_aggregation(extraction, effective_intent=effective_intent, query_operation=query_operation)
    answer_fact_field = parser._infer_answer_fact_field(
        extraction,
        effective_intent=effective_intent,
        query_operation=query_operation,
    )
    result_reference = extraction.result_reference
    if aggregation is not None and aggregation.type in {"largest", "smallest"}:
        result_reference = None
    intent = parser._intent_from_query_operation(query_operation)
    fallback_time_range = time_range or TimeRange(start=today - timedelta(days=30), end=today, granularity="day")
    intent_spec = derive_query_intent_spec_from_fields(
        intent=intent,
        filters=filters,
        aggregation=aggregation,
        answer_fact_field=answer_fact_field,
    )
    return {
        "intent": intent,
        "query_operation": query_operation,
        "time_range": fallback_time_range,
        "filters": filters,
        "aggregation": aggregation,
        "accounts_scope": "all",
        "account_name": None,
        "amount_check": None,
        "item_name": None,
        "analysis_type": "immediate",
        "result_limit": result_limit,
        "result_reference": result_reference,
        "answer_fact_field": answer_fact_field,
        "intent_spec": intent_spec,
    }


def resolve_effective_intent(
    raw_query: str | None,
    intent: ExtractionIntent,
    *,
    request_shape: QueryRequestShape | None = None,
    fact_query_kind: FactQueryKind | None = None,
    answer_fact_field: str | None = None,
) -> ExtractionIntent:
    raw_lower = (raw_query or "").strip().lower()
    if request_shape == QueryRequestShape.FACT:
        return ExtractionIntent.SINGLE_TRANSACTION
    if request_shape in {
        QueryRequestShape.ANALYTICS,
        QueryRequestShape.GROUPED_SUMMARY,
        QueryRequestShape.COMPARISON,
        QueryRequestShape.AFFORDABILITY,
        QueryRequestShape.LIST,
    }:
        return intent
    if fact_query_kind is not None or answer_fact_field in {
        "date",
        "counterparty",
        "amount",
        "bank",
    }:
        return ExtractionIntent.SINGLE_TRANSACTION
    if intent == ExtractionIntent.BENEFICIARY_SUMMARY and is_single_transaction_fact_query(raw_lower):
        return ExtractionIntent.SINGLE_TRANSACTION
    if intent == ExtractionIntent.TRANSACTION_LIST and is_aggregate_total_query(raw_lower):
        return ExtractionIntent.SPENDING_TOTAL
    return intent


def is_single_transaction_fact_query(raw_query: str) -> bool:
    if not raw_query:
        return False
    normalized = f" {' '.join(raw_query.split())} "
    if any(
        phrase in normalized
        for phrase in (
            " who did i ",
            " who do i ",
            " top ",
            " most ",
            " people ",
            " recipients ",
            " to the most ",
        )
    ):
        return False

    has_fact_cue = (
        normalized.startswith(" when ")
        or " when did " in normalized
        or " when last did " in normalized
        or normalized.startswith(" which bank ")
        or normalized.startswith(" what bank ")
        or normalized.startswith(" how much was ")
        or normalized.startswith(" how much did ")
    )
    if not has_fact_cue:
        return False

    return any(
        phrase in normalized
        for phrase in (
            " pay ",
            " paid ",
            " send ",
            " sent ",
            " transfer ",
            " transferred ",
            " receive ",
            " received ",
        )
    )


def intent_from_query_operation(query_operation: QueryOperation) -> QueryIntent:
    if query_operation == QueryOperation.LIST_TRANSACTIONS:
        return QueryIntent.TRANSACTION_LIST
    if query_operation == QueryOperation.SEARCH_SINGLE_TRANSACTION:
        return QueryIntent.TRANSACTION_SEARCH
    if query_operation in {
        QueryOperation.SUM_TRANSACTIONS,
        QueryOperation.COUNT_TRANSACTIONS,
        QueryOperation.AVERAGE_TRANSACTIONS,
        QueryOperation.RANK_LARGEST_TRANSACTION,
        QueryOperation.RANK_SMALLEST_TRANSACTION,
        QueryOperation.BREAKDOWN_TRANSACTIONS,
    }:
        return QueryIntent.ANALYTICS_SUMMARY
    if query_operation == QueryOperation.COMPARE_PERIODS:
        return QueryIntent.TIME_COMPARISON
    if query_operation == QueryOperation.SUMMARIZE_BENEFICIARIES:
        return QueryIntent.BENEFICIARY_SUMMARY
    return QueryIntent.AFFORDABILITY


def infer_query_operation(extraction: QueryExtractionResult, *, effective_intent: ExtractionIntent) -> QueryOperation:
    if extraction.query_operation is not None:
        return extraction.query_operation
    if extraction.request_shape == QueryRequestShape.FACT or (
        extraction.fact_query_kind is not None
        and extraction.request_shape
        not in {
            QueryRequestShape.ANALYTICS,
            QueryRequestShape.GROUPED_SUMMARY,
            QueryRequestShape.COMPARISON,
            QueryRequestShape.AFFORDABILITY,
            QueryRequestShape.LIST,
        }
    ):
        return QueryOperation.SEARCH_SINGLE_TRANSACTION
    aggregation_type = extraction.aggregation.type if extraction.aggregation is not None else None
    if aggregation_type == "count":
        return QueryOperation.COUNT_TRANSACTIONS
    if aggregation_type == "average":
        return QueryOperation.AVERAGE_TRANSACTIONS
    if aggregation_type == "largest":
        return QueryOperation.RANK_LARGEST_TRANSACTION
    if aggregation_type == "smallest":
        return QueryOperation.RANK_SMALLEST_TRANSACTION
    if aggregation_type == "breakdown":
        return QueryOperation.BREAKDOWN_TRANSACTIONS
    if effective_intent == ExtractionIntent.SINGLE_TRANSACTION:
        return QueryOperation.SEARCH_SINGLE_TRANSACTION
    if effective_intent == ExtractionIntent.SPENDING_TOTAL:
        return QueryOperation.SUM_TRANSACTIONS
    if effective_intent == ExtractionIntent.CATEGORY_BREAKDOWN:
        return QueryOperation.BREAKDOWN_TRANSACTIONS
    if effective_intent == ExtractionIntent.BENEFICIARY_SUMMARY:
        return QueryOperation.SUMMARIZE_BENEFICIARIES
    if effective_intent == ExtractionIntent.TIME_COMPARISON:
        return QueryOperation.COMPARE_PERIODS
    if effective_intent == ExtractionIntent.AFFORDABILITY:
        return QueryOperation.CHECK_AFFORDABILITY
    return QueryOperation.LIST_TRANSACTIONS


def is_aggregate_total_query(raw_query: str) -> bool:
    if not raw_query:
        return False
    if not any(cue in raw_query for cue in ("how much", "total", "sum")):
        return False
    return any(
        cue in raw_query
        for cue in (
            "spend", "spent", "spending", "expense", "expenses", "pay", "paid",
            "send", "sent", "transfer", "transferred", "receive", "received",
            "credit", "credited", "income", "inflow",
        )
    )


def resolve_result_limit(raw_limit: int | None, *, effective_intent: ExtractionIntent) -> int | None:
    result_limit = raw_limit
    if effective_intent == ExtractionIntent.SINGLE_TRANSACTION:
        result_limit = result_limit or 1
    if result_limit:
        result_limit = min(result_limit, QUERY_LIMITS["max_results"])
    return result_limit


def build_time_range(extraction: QueryExtractionResult, *, today: date) -> TimeRange | None:
    if not extraction.time_range:
        return None
    days_back = extraction.time_range.days_back
    period_lower = (extraction.time_range.period or "").strip().lower()
    reference_type = extraction.time_range.reference_type
    if reference_type == TimeReference.EXPLICIT and period_lower:
        explicit_range = resolve_period_to_range(period_lower, today=today)
        if explicit_range is not None:
            return explicit_range
    if period_lower == "today":
        days_back = 0
    elif period_lower == "yesterday":
        days_back = 1
    if days_back is None:
        days_back = 30
    if reference_type == TimeReference.ALL_TIME:
        days_back = QUERY_LIMITS["max_lookback_days"]
    elif reference_type == TimeReference.UNSPECIFIED:
        days_back = 30
    range_start = today - timedelta(days=days_back)
    range_end = today
    if period_lower == "today":
        range_start = today
        range_end = today
    elif period_lower == "yesterday":
        yesterday = today - timedelta(days=1)
        range_start = yesterday
        range_end = yesterday
    return TimeRange(start=range_start, end=range_end, granularity="day")


def infer_transaction_type(
    *,
    extracted_transaction_type: str | None,
    raw_query: str | None,
    effective_intent: ExtractionIntent,
) -> Literal["credit", "debit"] | None:
    transaction_type = (extracted_transaction_type or "").strip().lower() or None
    if not transaction_type:
        raw_lower = (raw_query or "").lower()
        has_explicit_credit_intent = any(k in raw_lower for k in ("received", "credited", "income", "salary", "sent me", "from "))
        if has_explicit_credit_intent:
            transaction_type = "credit"

        is_expense_query = not has_explicit_credit_intent and effective_intent in (
            ExtractionIntent.SPENDING_TOTAL,
            ExtractionIntent.CATEGORY_BREAKDOWN,
            ExtractionIntent.BENEFICIARY_SUMMARY,
        )
        if not has_explicit_credit_intent and not is_expense_query and raw_lower:
            if any(k in raw_lower for k in ("spending", "expense", "spent", "cost", "paid", "send", "sent", "transfer", "transferred")):
                is_expense_query = True
        if is_expense_query:
            transaction_type = "debit"
    if transaction_type in {"credit", "debit"}:
        return cast(Literal["credit", "debit"], transaction_type)
    return None


def build_filters(
    parser: Any,
    extraction: QueryExtractionResult,
    *,
    effective_intent: ExtractionIntent,
    query_operation: QueryOperation,
) -> Filters | None:
    if not extraction.filters:
        return None
    group_by = (extraction.aggregation.group_by or "").strip().lower() if extraction.aggregation else ""
    counterparty = parser._normalize_counterparty_filter(extraction.filters.recipient)
    transaction_type = None
    if group_by not in {"transaction_type", "type"}:
        transaction_type = parser._infer_transaction_type(
            extracted_transaction_type=extraction.filters.transaction_type,
            raw_query=extraction.raw_query,
            effective_intent=effective_intent,
        )
    return Filters(
        merchant=[extraction.filters.narration_keyword] if extraction.filters.narration_keyword else None,
        counterparty=[counterparty] if counterparty else None,
        category=[extraction.filters.category] if extraction.filters.category else None,
        min_amount=extraction.filters.min_amount,
        max_amount=extraction.filters.max_amount,
        transaction_type=transaction_type,
        account_filter=extraction.filters.bank,
    )


def normalize_counterparty_filter(placeholders: set[str] | frozenset[str], recipient: str | None) -> str | None:
    normalized = " ".join((recipient or "").strip().split())
    if not normalized:
        return None
    lowered = normalized.casefold()
    if lowered in placeholders or lowered.startswith("unknown "):
        return None
    return normalized


def infer_answer_fact_field(
    extraction: QueryExtractionResult,
    *,
    effective_intent: ExtractionIntent,
    query_operation: QueryOperation,
) -> Literal["date", "counterparty", "amount", "bank"] | None:
    if extraction.answer_fact_field in {"date", "counterparty", "amount", "bank"}:
        return cast(Literal["date", "counterparty", "amount", "bank"], extraction.answer_fact_field)
    if extraction.fact_query_kind in {
        FactQueryKind.DATE,
        FactQueryKind.COUNTERPARTY,
        FactQueryKind.AMOUNT,
        FactQueryKind.BANK,
    }:
        return cast(Literal["date", "counterparty", "amount", "bank"], extraction.fact_query_kind.value)
    if effective_intent in {
        ExtractionIntent.SPENDING_TOTAL,
        ExtractionIntent.CATEGORY_BREAKDOWN,
        ExtractionIntent.BENEFICIARY_SUMMARY,
        ExtractionIntent.TIME_COMPARISON,
        ExtractionIntent.AFFORDABILITY,
    }:
        return None
    if query_operation not in {QueryOperation.LIST_TRANSACTIONS, QueryOperation.SEARCH_SINGLE_TRANSACTION}:
        return None
    raw_query = f" {(extraction.raw_query or '').strip().lower()} "
    if raw_query == "  ":
        return None
    if raw_query.startswith(" when ") or " when did " in raw_query:
        return "date"
    if raw_query.startswith(" who ") or " who sent " in raw_query or " who paid " in raw_query:
        return "counterparty"
    if raw_query.startswith(" which bank ") or raw_query.startswith(" what bank "):
        return "bank"
    if raw_query.startswith(" how much was ") or raw_query.startswith(" how much did i pay for "):
        return "amount"
    return None


def coerce_aggregation_type(agg_type: str) -> Literal["sum", "average", "count", "largest", "smallest", "breakdown"]:
    return cast(Literal["sum", "average", "count", "largest", "smallest", "breakdown"], agg_type if agg_type in {"sum", "average", "count", "largest", "smallest", "breakdown"} else "sum")


def coerce_group_by(group_by: str | None) -> Literal["category", "merchant", "day", "account", "transaction_type"] | None:
    normalized = (group_by or "").strip().lower()
    if normalized == "type":
        normalized = "transaction_type"
    if normalized in {"category", "merchant", "day", "account", "transaction_type"}:
        return cast(Literal["category", "merchant", "day", "account", "transaction_type"], normalized)
    return None


def infer_breakdown_group_by(extraction: QueryExtractionResult) -> Literal["category", "merchant", "day", "account", "transaction_type"] | None:
    raw_lower = f" {(extraction.raw_query or '').strip().lower()} "
    if any(hint in raw_lower for hint in (" by account ", " per account ", " by bank ", " per bank ", " across accounts ")):
        return "account"
    extracted_group_by = coerce_group_by(extraction.aggregation.group_by) if extraction.aggregation is not None else None
    if extracted_group_by is not None:
        return extracted_group_by
    return None


def coerce_sort_by(sort_by: str | None) -> Literal["amount", "count"] | None:
    if sort_by in {"amount", "count"}:
        return cast(Literal["amount", "count"], sort_by)
    return None


def build_aggregation_from_extracted(extraction: QueryExtractionResult, *, effective_intent: ExtractionIntent) -> Aggregation | None:
    if not extraction.aggregation:
        return None
    agg_type = extraction.aggregation.type or "sum"
    if effective_intent == ExtractionIntent.CATEGORY_BREAKDOWN and agg_type == "sum":
        agg_type = "breakdown"
    aggregation = Aggregation(
        type=coerce_aggregation_type(agg_type),
        group_by=coerce_group_by(extraction.aggregation.group_by),
        limit=extraction.aggregation.limit or 5,
        sort_by=coerce_sort_by(extraction.aggregation.sort_by),
    )
    lexical_group_by = infer_breakdown_group_by(extraction)
    if aggregation.type == "breakdown" and lexical_group_by is not None:
        aggregation.group_by = lexical_group_by
    if agg_type == "breakdown" and not aggregation.group_by:
        aggregation.group_by = "category"
    if effective_intent == ExtractionIntent.BENEFICIARY_SUMMARY and aggregation.sort_by is None:
        aggregation.sort_by = "count"
    return aggregation


def build_default_aggregation(
    parser: Any,
    extraction: QueryExtractionResult,
    *,
    effective_intent: ExtractionIntent,
    query_operation: QueryOperation,
) -> Aggregation | None:
    raw_lower = (extraction.raw_query or "").strip().lower()
    if query_operation == QueryOperation.RANK_LARGEST_TRANSACTION or any(cue in raw_lower for cue in ("largest", "highest", "biggest", "max", "maximum")):
        return Aggregation(type="largest", limit=1)
    if query_operation == QueryOperation.RANK_SMALLEST_TRANSACTION or any(cue in raw_lower for cue in ("smallest", "lowest", "least", "minimum", "min")):
        return Aggregation(type="smallest", limit=1)
    if query_operation == QueryOperation.COUNT_TRANSACTIONS:
        return Aggregation(type="count", limit=5)
    if query_operation == QueryOperation.AVERAGE_TRANSACTIONS:
        return Aggregation(type="average", limit=5)
    if query_operation == QueryOperation.BREAKDOWN_TRANSACTIONS:
        group_by = parser._infer_breakdown_group_by(extraction)
        return Aggregation(type="breakdown", group_by=group_by or "category", limit=5)
    if effective_intent == ExtractionIntent.SPENDING_TOTAL:
        return Aggregation(type="sum", limit=5)
    if effective_intent == ExtractionIntent.CATEGORY_BREAKDOWN:
        return Aggregation(type="breakdown", group_by=parser._infer_breakdown_group_by(extraction) or "category")
    if effective_intent == ExtractionIntent.BENEFICIARY_SUMMARY:
        return Aggregation(type="sum", limit=5, sort_by="count")
    return None


def build_aggregation(
    parser: Any,
    extraction: QueryExtractionResult,
    *,
    effective_intent: ExtractionIntent,
    query_operation: QueryOperation,
) -> Aggregation | None:
    extracted_aggregation = parser._build_aggregation_from_extracted(extraction, effective_intent=effective_intent)
    if extracted_aggregation is not None:
        return parser._normalize_operation_aggregation(
            parser._normalize_extrema_aggregation(extracted_aggregation, raw_query=extraction.raw_query),
            query_operation=query_operation,
        )
    return parser._build_default_aggregation(
        extraction,
        effective_intent=effective_intent,
        query_operation=query_operation,
    )


def normalize_operation_aggregation(aggregation: Aggregation | None, *, query_operation: QueryOperation) -> Aggregation | None:
    if aggregation is None:
        return None
    operation_to_type = {
        QueryOperation.SUM_TRANSACTIONS: "sum",
        QueryOperation.COUNT_TRANSACTIONS: "count",
        QueryOperation.AVERAGE_TRANSACTIONS: "average",
        QueryOperation.RANK_LARGEST_TRANSACTION: "largest",
        QueryOperation.RANK_SMALLEST_TRANSACTION: "smallest",
        QueryOperation.BREAKDOWN_TRANSACTIONS: "breakdown",
    }
    target_type = operation_to_type.get(query_operation)
    if target_type is None:
        return aggregation
    aggregation.type = cast(Literal["sum", "average", "count", "largest", "smallest", "breakdown"], target_type)
    if query_operation == QueryOperation.BREAKDOWN_TRANSACTIONS and aggregation.group_by is None:
        aggregation.group_by = "category"
    if query_operation in {QueryOperation.RANK_LARGEST_TRANSACTION, QueryOperation.RANK_SMALLEST_TRANSACTION}:
        aggregation.limit = 1
    return aggregation


def normalize_extrema_aggregation(aggregation: Aggregation | None, *, raw_query: str | None) -> Aggregation | None:
    if aggregation is None:
        return None
    raw_lower = (raw_query or "").strip().lower()
    singular_extrema = (
        ("largest", ("largest", "highest", "biggest", "max", "maximum")),
        ("smallest", ("smallest", "lowest", "least", "minimum", "min")),
    )
    for agg_type, cues in singular_extrema:
        if any(cue in raw_lower for cue in cues):
            aggregation.type = cast(Literal["sum", "average", "count", "largest", "smallest", "breakdown"], agg_type)
            aggregation.limit = 1
            return aggregation
    if aggregation.type in {"largest", "smallest"}:
        aggregation.limit = 1
    return aggregation
