"""Intent and operation inference for query compiler output."""

from __future__ import annotations

from typing import Literal, cast

from apps.chat.src.agent.workers.query.capabilities import QUERY_LIMITS
from apps.chat.src.agent.workers.query.models.domain import QueryFactField, QueryIntent, QueryOperation
from apps.chat.src.agent.workers.query.models.extraction import (
    ExtractionIntent,
    FactQueryKind,
    QueryExtractionResult,
    QueryRequestShape,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def resolve_effective_intent_from_extraction(extraction: QueryExtractionResult) -> ExtractionIntent:
    effective_intent = resolve_effective_intent(
        extraction.raw_query,
        extraction.intent,
        request_shape=extraction.request_shape,
        fact_query_kind=extraction.fact_query_kind,
        answer_fact_field=extraction.answer_fact_field,
    )
    if effective_intent != extraction.intent:
        logger.info(
            "query_parser_intent_recovered_from_list_misclassification",
            original_intent=extraction.intent.value,
            recovered_intent=effective_intent.value,
        )
    return effective_intent


def resolve_effective_intent(
    raw_query: str | None,
    intent: ExtractionIntent,
    *,
    request_shape: QueryRequestShape | None = None,
    fact_query_kind: FactQueryKind | None = None,
    answer_fact_field: str | None = None,
) -> ExtractionIntent:
    raw_lower = (raw_query or "").strip().lower()
    if request_shape in {QueryRequestShape.FACT, QueryRequestShape.EXISTENCE}:
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
        "status",
        "description",
        "reference",
        "account",
        "direction",
        "category",
    }:
        return ExtractionIntent.SINGLE_TRANSACTION
    if intent == ExtractionIntent.TRANSACTION_LIST and is_aggregate_total_query(raw_lower):
        return ExtractionIntent.SPENDING_TOTAL
    return intent


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
    if extraction.request_shape == QueryRequestShape.EXISTENCE:
        return QueryOperation.SUM_TRANSACTIONS
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
            "spend",
            "spent",
            "spending",
            "expense",
            "expenses",
            "pay",
            "paid",
            "send",
            "sent",
            "transfer",
            "transferred",
            "receive",
            "received",
            "credit",
            "credited",
            "income",
            "inflow",
        )
    )


def resolve_result_limit(
    raw_limit: int | None,
    *,
    effective_intent: ExtractionIntent,
    request_shape: QueryRequestShape | None = None,
    result_reference: Literal["latest", "oldest"] | None = None,
) -> int | None:
    result_limit = raw_limit
    if effective_intent == ExtractionIntent.SINGLE_TRANSACTION and result_limit is None:
        if request_shape == QueryRequestShape.FACT and result_reference is None:
            return None
        result_limit = result_limit or 1
    if result_limit:
        result_limit = min(result_limit, QUERY_LIMITS["max_results"])
    return result_limit


def infer_result_reference(
    extraction: QueryExtractionResult,
    *,
    query_operation: QueryOperation,
) -> Literal["latest", "oldest"] | None:
    if extraction.result_reference in {"latest", "oldest"}:
        return cast(Literal["latest", "oldest"], extraction.result_reference)
    if query_operation != QueryOperation.SEARCH_SINGLE_TRANSACTION:
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
