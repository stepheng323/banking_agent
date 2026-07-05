"""QueryIR and runtime contract compilation helpers."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, cast

from banking.transactions.query.compiler import aggregation as aggregation_compiler
from banking.transactions.query.compiler import filtering as filter_compiler
from banking.transactions.query.compiler import operations as operation_compiler
from banking.transactions.query.compiler import time_ranges as time_compiler
from banking.transactions.query.compiler.lexical_recovery import (
    recover_known_fragile_query_shapes,
)
from banking.transactions.query.models.domain import (
    Aggregation,
    Filters,
    QueryContractRequestShape,
    QueryExecutionContract,
    QueryFactField,
    QueryIntent,
    QueryIR,
    derive_query_intent_spec_from_fields,
)
from banking.transactions.query.models.extraction import QueryExtractionResult
from banking.transactions.query.utils.timezone import lagos_today


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
    compiled = parser._compile_query_fields_from_extraction(extraction, today=base_today, language=language)
    time_range = compiled["time_range"] or time_compiler.default_rolling_time_range(base_today)
    comparison = time_compiler.build_comparison_directive(
        extraction,
        intent=cast(QueryIntent, compiled["intent"]),
        current_range=time_range,
        today=base_today,
    )

    return QueryIR(
        intent=cast(QueryIntent, compiled["intent"]),
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
        answer_fact_field=cast(QueryFactField | None, compiled["answer_fact_field"]),
        request_shape=cast(QueryContractRequestShape | None, compiled["request_shape"]),
        comparison=comparison,
        continuation_type=continuation_type,
        continuation_delta_type=continuation_delta_type,
        intent_spec=cast(Any, compiled["intent_spec"]),
    )


def build_execution_contract_from_ir(query_ir: QueryIR) -> QueryExecutionContract:
    return QueryExecutionContract.from_query_ir(query_ir)


def compile_query_fields_from_extraction(
    parser: Any,
    extraction: QueryExtractionResult,
    *,
    today: date,
    language: str = "en",
) -> dict[str, Any]:
    # The compiler keeps the same precedence as parser finalization:
    # typed extraction, then derived shape, then locale-aware semantic repair.
    extraction = recover_known_fragile_query_shapes(extraction.model_copy(deep=True), language=language)
    extraction = operation_compiler.normalize_query_extraction(extraction)
    intent = extraction.intent

    result_reference = operation_compiler.infer_result_reference(extraction, intent=intent)
    answer_fact_field = operation_compiler.infer_answer_fact_field(extraction)
    result_limit = operation_compiler.resolve_result_limit(
        extraction.result_limit,
        effective_intent=intent,
        request_shape=extraction.request_shape,
        result_reference=result_reference,
    )
    if result_limit is None:
        result_limit = operation_compiler.infer_query_result_limit(extraction, intent=intent)
    time_range = time_compiler.build_time_range(
        extraction,
        today=today,
        intent=intent,
        answer_fact_field=answer_fact_field,
        result_reference=result_reference,
    )
    filters = filter_compiler.build_filters(extraction, intent=intent)
    aggregation = aggregation_compiler.build_aggregation(
        extraction,
        intent=intent,
    )
    if aggregation is not None and aggregation.type in {"largest", "smallest"}:
        result_reference = None

    fallback_time_range = time_range or time_compiler.default_rolling_time_range(today)
    intent_spec = derive_query_intent_spec_from_fields(
        intent=intent,
        filters=filters,
        aggregation=aggregation,
        answer_fact_field=answer_fact_field,
        request_shape=extraction.request_shape.value if extraction.request_shape is not None else None,
    )
    amount_check = None
    if intent == QueryIntent.AFFORDABILITY and extraction.filters:
        amount_check = extraction.filters.min_amount or extraction.filters.max_amount

    return {
        "intent": intent,
        "time_range": fallback_time_range,
        "filters": filters,
        "aggregation": aggregation,
        "accounts_scope": "all",
        "account_name": None,
        "amount_check": amount_check,
        "item_name": None,
        "analysis_type": "immediate",
        "result_limit": result_limit,
        "result_reference": result_reference,
        "answer_fact_field": answer_fact_field,
        "request_shape": extraction.request_shape.value if extraction.request_shape is not None else None,
        "intent_spec": intent_spec,
    }
