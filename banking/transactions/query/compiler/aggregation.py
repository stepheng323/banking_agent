"""Aggregation inference for query compiler output."""

from __future__ import annotations

from typing import Literal, cast

from banking.transactions.query.models.domain import Aggregation, QueryIntent
from banking.transactions.query.models.extraction import QueryExtractionResult

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
    if any(
        hint in raw_lower
        for hint in (
            " by account ",
            " per account ",
            " by accounts ",
            " by bank ",
            " by banks ",
            " per bank ",
            " per banks ",
            " across accounts ",
        )
    ):
        return "account"
    if any(
        hint in raw_lower for hint in (" by category ", " per category ")
    ):
        return "category"
    if any(
        hint in raw_lower for hint in (" by merchant ", " per merchant ", " by vendor ", " per vendor ")
    ):
        return "merchant"
    if any(
        hint in raw_lower for hint in (" by day ", " per day ", " daily ")
    ):
        return "day"
    if any(
        hint in raw_lower for hint in (" by type ", " per type ")
    ):
        return "transaction_type"
    extracted_group_by = (
        coerce_group_by(extraction.aggregation.group_by) if extraction.aggregation is not None else None
    )
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
    intent: QueryIntent,
) -> Aggregation | None:
    if not extraction.aggregation:
        return None
    agg_type = extraction.aggregation.type or "sum"
    extracted_group_by = coerce_group_by(extraction.aggregation.group_by)
    if intent == QueryIntent.ANALYTICS_SUMMARY and agg_type == "sum" and extracted_group_by is not None:
        agg_type = "breakdown"
    aggregation = Aggregation(
        type=coerce_aggregation_type(agg_type),
        group_by=extracted_group_by,
        limit=extraction.aggregation.limit or 5,
        sort_by=coerce_sort_by(extraction.aggregation.sort_by),
    )
    lexical_group_by = infer_breakdown_group_by(extraction)
    if aggregation.type == "breakdown" and lexical_group_by is not None:
        aggregation.group_by = lexical_group_by
    if agg_type == "breakdown" and not aggregation.group_by:
        aggregation.group_by = "category"
    if intent == QueryIntent.BENEFICIARY_SUMMARY and aggregation.sort_by is None:
        aggregation.sort_by = "amount"
    return aggregation


def build_default_aggregation(
    extraction: QueryExtractionResult,
    *,
    intent: QueryIntent,
) -> Aggregation | None:
    raw_lower = (extraction.raw_query or "").strip().lower()
    if any(cue in raw_lower for cue in ("largest", "highest", "biggest", "max", "maximum")):
        return Aggregation(type="largest", limit=1)
    if any(cue in raw_lower for cue in ("smallest", "lowest", "least", "minimum", "min")):
        return Aggregation(type="smallest", limit=1)

    if intent == QueryIntent.ANALYTICS_SUMMARY:
        if "count" in raw_lower or "how many" in raw_lower:
            return Aggregation(type="count", limit=5)
        if "average" in raw_lower:
            return Aggregation(type="average", limit=5)
        if (
            "break down" in raw_lower
            or "breakdown" in raw_lower
            or "where did my money go" in raw_lower
            or "where my money went" in raw_lower
            or "what did i spend on" in raw_lower
            or infer_breakdown_group_by(extraction) is not None
        ):
            return Aggregation(type="breakdown", group_by=infer_breakdown_group_by(extraction) or "category")
        return Aggregation(type="sum", limit=5)

    if intent == QueryIntent.BENEFICIARY_SUMMARY:
        return Aggregation(type="sum", limit=5, sort_by="amount")

    return None


def build_aggregation(
    extraction: QueryExtractionResult,
    *,
    intent: QueryIntent,
) -> Aggregation | None:
    extracted_aggregation = build_aggregation_from_extracted(extraction, intent=intent)
    if extracted_aggregation is not None:
        return normalize_extrema_aggregation(extracted_aggregation, raw_query=extraction.raw_query)
    return build_default_aggregation(
        extraction,
        intent=intent,
    )


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
