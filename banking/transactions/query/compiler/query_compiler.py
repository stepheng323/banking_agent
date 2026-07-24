"""Normalization helpers used by the typed query-operation compiler."""

from __future__ import annotations

from datetime import date
from typing import Any

from banking.transactions.query.compiler import aggregation as aggregation_compiler
from banking.transactions.query.compiler import filtering as filter_compiler
from banking.transactions.query.compiler import operations as operation_compiler
from banking.transactions.query.compiler import time_ranges as time_compiler
from banking.transactions.query.compiler.lexical_recovery import (
    recover_known_fragile_query_shapes,
)
from banking.transactions.query.models.domain import QueryIntent
from banking.transactions.query.models.extraction import QueryExtractionResult


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
    }
