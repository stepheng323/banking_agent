"""Aggregation inference for query compiler output."""

from __future__ import annotations

from typing import Literal, cast

from apps.chat.src.agent.workers.query.models.domain import Aggregation, QueryOperation
from apps.chat.src.agent.workers.query.models.extraction import ExtractionIntent, QueryExtractionResult

AggregationType = Literal["sum", "average", "count", "largest", "smallest", "breakdown"]
GroupByField = Literal["category", "merchant", "day", "account", "transaction_type"]


def coerce_aggregation_type(agg_type: str) -> AggregationType:
    if agg_type in {"sum", "average", "count", "largest", "smallest", "breakdown"}:
        return cast(AggregationType, agg_type)
    return "sum"


def coerce_group_by(group_by: str | None) -> GroupByField | None:
    normalized = (group_by or "").strip().lower()
    if normalized == "type":
        normalized = "transaction_type"
    if normalized in {"category", "merchant", "day", "account", "transaction_type"}:
        return cast(GroupByField, normalized)
    return None


def infer_breakdown_group_by(extraction: QueryExtractionResult) -> GroupByField | None:
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


def build_aggregation_from_extracted(
    extraction: QueryExtractionResult,
    *,
    effective_intent: ExtractionIntent,
) -> Aggregation | None:
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
    extraction: QueryExtractionResult,
    *,
    effective_intent: ExtractionIntent,
    query_operation: QueryOperation,
) -> Aggregation | None:
    raw_lower = (extraction.raw_query or "").strip().lower()
    if query_operation == QueryOperation.RANK_LARGEST_TRANSACTION or any(
        cue in raw_lower for cue in ("largest", "highest", "biggest", "max", "maximum")
    ):
        return Aggregation(type="largest", limit=1)
    if query_operation == QueryOperation.RANK_SMALLEST_TRANSACTION or any(
        cue in raw_lower for cue in ("smallest", "lowest", "least", "minimum", "min")
    ):
        return Aggregation(type="smallest", limit=1)
    if query_operation == QueryOperation.COUNT_TRANSACTIONS:
        return Aggregation(type="count", limit=5)
    if query_operation == QueryOperation.AVERAGE_TRANSACTIONS:
        return Aggregation(type="average", limit=5)
    if query_operation == QueryOperation.BREAKDOWN_TRANSACTIONS:
        group_by = infer_breakdown_group_by(extraction)
        return Aggregation(type="breakdown", group_by=group_by or "category", limit=5)
    if effective_intent == ExtractionIntent.SPENDING_TOTAL:
        return Aggregation(type="sum", limit=5)
    if effective_intent == ExtractionIntent.CATEGORY_BREAKDOWN:
        return Aggregation(type="breakdown", group_by=infer_breakdown_group_by(extraction) or "category")
    if effective_intent == ExtractionIntent.BENEFICIARY_SUMMARY:
        return Aggregation(type="sum", limit=5, sort_by="count")
    return None


def build_aggregation(
    extraction: QueryExtractionResult,
    *,
    effective_intent: ExtractionIntent,
    query_operation: QueryOperation,
) -> Aggregation | None:
    extracted_aggregation = build_aggregation_from_extracted(extraction, effective_intent=effective_intent)
    if extracted_aggregation is not None:
        return normalize_operation_aggregation(
            normalize_extrema_aggregation(extracted_aggregation, raw_query=extraction.raw_query),
            query_operation=query_operation,
        )
    return build_default_aggregation(
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
    aggregation.type = cast(AggregationType, target_type)
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
            aggregation.type = cast(AggregationType, agg_type)
            aggregation.limit = 1
            return aggregation
    if aggregation.type in {"largest", "smallest"}:
        aggregation.limit = 1
    return aggregation
