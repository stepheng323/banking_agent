"""Intent and operation inference for query compiler output."""

from __future__ import annotations

import re
from typing import Literal, cast

from banking.transactions.query.capabilities import QUERY_LIMITS
from banking.transactions.query.models.domain import QueryFactField, QueryIntent
from banking.transactions.query.models.extraction import (
    FactQueryKind,
    QueryAggregation,
    QueryExtractionResult,
    QueryFilters,
    QueryRequestShape,
)


def normalize_query_extraction(extraction: QueryExtractionResult) -> QueryExtractionResult:
    extraction = _normalize_cash_flow_extraction(extraction)
    extraction = _normalize_affordability_extraction(extraction)

    # 1. Transaction detail ambiguity handling
    if extraction.intent == QueryIntent.TRANSACTION_DETAIL:
        if not extraction.filters or (
            not extraction.filters.recipient
            and not extraction.filters.narration_keyword
            and not extraction.filters.bank
            and not extraction.answer_fact_field
        ):
            extraction.intent = QueryIntent.QUERY_CLARIFICATION

    # 2. Affordability validation
    if extraction.intent == QueryIntent.AFFORDABILITY:
        has_amount = False
        if extraction.filters:
            if (
                extraction.filters.min_amount is not None
                or extraction.filters.max_amount is not None
                or getattr(extraction.filters, "amount", None) is not None
            ):
                has_amount = True
        if not has_amount:
            extraction.intent = QueryIntent.QUERY_CLARIFICATION

    return extraction


_CASH_FLOW_DIRECT_RE = re.compile(r"\bcash\s*flow\b|\bcashflow\b", re.IGNORECASE)
_BIDIRECTIONAL_CASH_FLOW_RE = re.compile(
    r"\b(?:spend|spent|spending|expenses?|outflow|went out|go out|money go)\b"
    r".{0,80}\b(?:earn(?:ed)?|income|received?|came in|come in|inflow|credited?)\b"
    r"|"
    r"\b(?:earn(?:ed)?|income|received?|came in|come in|inflow|credited?)\b"
    r".{0,80}\b(?:spend|spent|spending|expenses?|outflow|went out|go out|money go)\b",
    re.IGNORECASE,
)
_INCOME_VS_OUTFLOW_RE = re.compile(
    r"\b(?:income|inflow|money in|came in|come in|earn(?:ed)?|received?)\b"
    r"\s*(?:vs|versus|and|against|compared to|compare(?:d)? with)\s*"
    r"\b(?:spending|expenses?|outflow|money out|went out|spent)\b"
    r"|"
    r"\b(?:spending|expenses?|outflow|money out|went out|spent)\b"
    r"\s*(?:vs|versus|and|against|compared to|compare(?:d)? with)\s*"
    r"\b(?:income|inflow|money in|came in|come in|earn(?:ed)?|received?)\b",
    re.IGNORECASE,
)
_AFFORDABILITY_PROBE_RE = re.compile(
    r"^\s*(?:can|could)\s+i\s+(?:afford|send|transfer|pay|spend|cover)\s+"
    r"(?:₦|ngn\s*)?(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<suffix>k|m|naira|ngn)?\b(?!\s+to\b)",
    re.IGNORECASE,
)
_SINGLE_DIRECTION_INFLOW_TOTAL_RE = re.compile(
    r"\b(?:how much|what amount|total)\b.{0,40}\b"
    r"(?:came in|come in|entered|was received|did i receive|have i received|received|credited)\b",
    re.IGNORECASE,
)
def _looks_like_single_direction_inflow_total(raw_query: str) -> bool:
    if not raw_query:
        return False
    if (
        _CASH_FLOW_DIRECT_RE.search(raw_query)
        or _BIDIRECTIONAL_CASH_FLOW_RE.search(raw_query)
        or _INCOME_VS_OUTFLOW_RE.search(raw_query)
    ):
        return False
    return bool(_SINGLE_DIRECTION_INFLOW_TOTAL_RE.search(raw_query))


def _normalize_cash_flow_extraction(extraction: QueryExtractionResult) -> QueryExtractionResult:
    """Validate semantic cash-flow meaning after parser/reasoner extraction."""
    raw_query = " ".join((extraction.raw_query or "").split())
    if _looks_like_single_direction_inflow_total(raw_query):
        extraction.intent = QueryIntent.ANALYTICS_SUMMARY
        extraction.filters.transaction_type = "credit"
        extraction.aggregation = QueryAggregation(type="sum")
        extraction.request_shape = QueryRequestShape.ANALYTICS
        return extraction

    if extraction.intent == QueryIntent.CASH_FLOW_SUMMARY:
        return extraction

    aggregation_group = (extraction.aggregation.group_by or "").strip().lower() if extraction.aggregation else ""
    if aggregation_group in {"transaction_type", "type"} and extraction.intent == QueryIntent.ANALYTICS_SUMMARY:
        if extraction.aggregation and extraction.aggregation.type != "breakdown":
            extraction.intent = QueryIntent.CASH_FLOW_SUMMARY
        return extraction

    if not raw_query:
        return extraction

    if (
        _CASH_FLOW_DIRECT_RE.search(raw_query)
        or _BIDIRECTIONAL_CASH_FLOW_RE.search(raw_query)
        or _INCOME_VS_OUTFLOW_RE.search(raw_query)
    ):
        extraction.intent = QueryIntent.CASH_FLOW_SUMMARY
        extraction.request_shape = QueryRequestShape.COMPARISON

    return extraction


def _parse_affordability_amount(raw_amount: str, suffix: str | None) -> float | None:
    try:
        amount = float(raw_amount.replace(",", ""))
    except ValueError:
        return None
    suffix = (suffix or "").strip().lower()
    if suffix == "k":
        amount *= 1_000
    elif suffix == "m":
        amount *= 1_000_000
    return amount if amount > 0 else None


def _normalize_affordability_extraction(extraction: QueryExtractionResult) -> QueryExtractionResult:
    """Recover read-only affordability probes that can look like transfer requests."""
    raw_query = " ".join((extraction.raw_query or "").split())
    if not raw_query:
        return extraction

    match = _AFFORDABILITY_PROBE_RE.search(raw_query)
    if not match:
        return extraction

    amount = _parse_affordability_amount(match.group("amount"), match.group("suffix"))
    if amount is None:
        return extraction

    extraction.intent = QueryIntent.AFFORDABILITY
    extraction.request_shape = QueryRequestShape.FACT
    filters = extraction.filters or QueryFilters()
    filters.min_amount = amount
    filters.max_amount = amount
    extraction.filters = filters
    return extraction


def infer_query_result_limit(
    extraction: QueryExtractionResult,
    *,
    intent: QueryIntent,
) -> int | None:
    if intent == QueryIntent.BENEFICIARY_SUMMARY and extraction.aggregation is not None:
        if extraction.aggregation.limit == 1:
            return 1
    return None




def resolve_result_limit(
    raw_limit: int | None,
    *,
    effective_intent: QueryIntent,
    request_shape: QueryRequestShape | None = None,
    result_reference: Literal["latest", "oldest"] | None = None,
) -> int | None:
    result_limit = raw_limit
    if effective_intent == QueryIntent.TRANSACTION_DETAIL and result_limit is None:
        if request_shape == QueryRequestShape.FACT and result_reference is None:
            return None
        result_limit = result_limit or 1
    if result_limit:
        result_limit = min(result_limit, QUERY_LIMITS["max_results"])
    return result_limit


def infer_result_reference(
    extraction: QueryExtractionResult,
    *,
    intent: QueryIntent,
) -> Literal["latest", "oldest"] | None:
    if extraction.result_reference in {"latest", "oldest"}:
        return cast(Literal["latest", "oldest"], extraction.result_reference)
    if intent != QueryIntent.TRANSACTION_DETAIL:
        return None
    raw_query = f" {(extraction.raw_query or '').strip().lower()} "
    if any(cue in raw_query for cue in (" last ", " latest ", " most recent ")):
        return "latest"
    if any(cue in raw_query for cue in (" first ", " earliest ", " oldest ")):
        return "oldest"
    return None


def infer_answer_fact_field(extraction: QueryExtractionResult) -> QueryFactField | None:
    if extraction.answer_fact_field in {
        "date",
        "counterparty",
        "amount",
        "bank",
        "status",
        "description",
        "reference",
        "account",
        "direction",
        "category",
    }:
        return extraction.answer_fact_field
    if extraction.fact_query_kind in {
        FactQueryKind.DATE,
        FactQueryKind.COUNTERPARTY,
        FactQueryKind.AMOUNT,
        FactQueryKind.BANK,
        FactQueryKind.STATUS,
        FactQueryKind.DESCRIPTION,
        FactQueryKind.REFERENCE,
        FactQueryKind.ACCOUNT,
        FactQueryKind.DIRECTION,
        FactQueryKind.CATEGORY,
    }:
        return cast(QueryFactField, extraction.fact_query_kind.value)
    return None
