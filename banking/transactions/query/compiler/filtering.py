"""Filter inference for query compiler output."""

from __future__ import annotations

import re
from typing import Literal, cast

from banking.transactions.query.models.domain import Filters, QueryIntent
from banking.transactions.query.models.extraction import QueryExtractionResult

COUNTERPARTY_PLACEHOLDERS = frozenset(
    {
        "unknown",
        "someone",
        "somebody",
        "person",
        "recipient",
        "sender",
        "merchant",
    }
)


def infer_transaction_type(
    *,
    extracted_transaction_type: str | None,
    raw_query: str | None,
    intent: QueryIntent,
) -> Literal["credit", "debit"] | None:
    transaction_type = (extracted_transaction_type or "").strip().lower() or None
    if not transaction_type:
        raw_lower = (raw_query or "").lower()
        has_explicit_credit_intent = any(
            token in raw_lower
            for token in (
                "received",
                "credited",
                "income",
                "salary",
                "sent me",
                "came in",
                "come in",
                "money in",
                "inflow",
                "earned",
                "from ",
            )
        )
        if has_explicit_credit_intent:
            transaction_type = "credit"

        is_expense_query = not has_explicit_credit_intent and intent in (
            QueryIntent.ANALYTICS_SUMMARY,
            QueryIntent.BENEFICIARY_SUMMARY,
        )
        if not has_explicit_credit_intent and not is_expense_query and raw_lower:
            expense_cues = (
                "spending",
                "expense",
                "spent",
                "cost",
                "paid",
                "send",
                "sent",
                "transfer",
                "transferred",
                "went out",
                "money go",
            )
            if any(token in raw_lower for token in expense_cues):
                is_expense_query = True
        if is_expense_query:
            transaction_type = "debit"
    if transaction_type in {"credit", "debit"}:
        return cast(Literal["credit", "debit"], transaction_type)
    return None


def build_filters(
    extraction: QueryExtractionResult,
    *,
    intent: QueryIntent,
) -> Filters | None:
    extracted_filters = extraction.filters
    group_by = (extraction.aggregation.group_by or "").strip().lower() if extraction.aggregation else ""
    counterparty = normalize_counterparty_filter(extracted_filters.recipient if extracted_filters else None)

    transaction_type = None
    if group_by not in {"transaction_type", "type"}:
        transaction_type = infer_transaction_type(
            extracted_transaction_type=extracted_filters.transaction_type if extracted_filters else None,
            raw_query=extraction.raw_query,
            intent=intent,
        )

    if not extracted_filters and not transaction_type and not counterparty:
        return None

    merchant = (
        [extracted_filters.narration_keyword]
        if extracted_filters is not None and extracted_filters.narration_keyword
        else None
    )

    return Filters(
        merchant=merchant,
        counterparty=[counterparty] if counterparty else None,
        category=([extracted_filters.category] if extracted_filters and extracted_filters.category else None),
        min_amount=extracted_filters.min_amount if extracted_filters else None,
        max_amount=extracted_filters.max_amount if extracted_filters else None,
        min_amount_inclusive=_infer_min_amount_inclusive(
            raw_query=extraction.raw_query,
            extracted_value=extracted_filters.min_amount if extracted_filters else None,
            extracted_inclusive=extracted_filters.min_amount_inclusive if extracted_filters else False,
        ),
        max_amount_inclusive=_infer_max_amount_inclusive(
            raw_query=extraction.raw_query,
            extracted_value=extracted_filters.max_amount if extracted_filters else None,
            extracted_inclusive=extracted_filters.max_amount_inclusive if extracted_filters else False,
        ),
        transaction_type=transaction_type,
        status=infer_status_filter(
            extracted_status=extracted_filters.status if extracted_filters else None,
            raw_query=extraction.raw_query,
        ),
        account_filter=extracted_filters.bank if extracted_filters else None,
    )


def infer_status_filter(
    *,
    extracted_status: str | None,
    raw_query: str | None,
) -> Literal["failed", "pending", "successful", "reversed"] | None:
    normalized = (extracted_status or "").strip().lower().replace("_", " ")
    if normalized in {"failed", "failure", "declined", "rejected"}:
        return "failed"
    if normalized in {"pending", "processing", "queued", "in progress"}:
        return "pending"
    if normalized in {"successful", "success", "posted", "completed", "complete", "confirmed"}:
        return "successful"
    if normalized in {"reversed", "refunded"}:
        return "reversed"

    raw_lower = f" {(raw_query or '').strip().lower()} "
    if not raw_lower.strip():
        return None
    if re.search(r"\b(?:failed|failure|declined|rejected|unsuccessful)\b", raw_lower):
        return "failed"
    if re.search(r"\b(?:pending|processing|queued|in progress)\b", raw_lower):
        return "pending"
    if re.search(r"\b(?:successful|success|posted|completed|complete|confirmed)\b", raw_lower):
        return "successful"
    if re.search(r"\b(?:reversed|refunded|refund)\b", raw_lower):
        return "reversed"
    return None


def normalize_counterparty_filter(recipient: str | None) -> str | None:
    normalized = " ".join((recipient or "").strip().split())
    if not normalized:
        return None
    lowered = normalized.casefold()
    if lowered in COUNTERPARTY_PLACEHOLDERS or lowered.startswith("unknown "):
        return None
    return normalized


def _infer_min_amount_inclusive(
    *,
    raw_query: str | None,
    extracted_value: float | None,
    extracted_inclusive: bool,
) -> bool:
    if extracted_value is None:
        return True
    raw_lower = f" {(raw_query or '').strip().lower()} "
    if re.search(r"\b(?:at least|minimum(?: of)?|not less than|and above)\b", raw_lower):
        return True
    if re.search(r"\b(?:above|over|more than|greater than)\b", raw_lower):
        return False
    return extracted_inclusive


def _infer_max_amount_inclusive(
    *,
    raw_query: str | None,
    extracted_value: float | None,
    extracted_inclusive: bool,
) -> bool:
    if extracted_value is None:
        return True
    raw_lower = f" {(raw_query or '').strip().lower()} "
    if re.search(r"\b(?:below|under|less than|lower than)\b", raw_lower):
        return False
    if re.search(r"\b(?:at most|maximum(?: of)?|up to|not more than)\b", raw_lower):
        return True
    return extracted_inclusive
