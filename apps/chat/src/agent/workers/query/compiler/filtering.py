"""Filter inference for query compiler output."""

from __future__ import annotations

from typing import Literal, cast

from apps.chat.src.agent.workers.query.models.domain import Filters
from apps.chat.src.agent.workers.query.models.extraction import ExtractionIntent, QueryExtractionResult

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
    effective_intent: ExtractionIntent,
) -> Literal["credit", "debit"] | None:
    transaction_type = (extracted_transaction_type or "").strip().lower() or None
    if not transaction_type:
        raw_lower = (raw_query or "").lower()
        has_explicit_credit_intent = any(
            token in raw_lower for token in ("received", "credited", "income", "salary", "sent me", "from ")
        )
        if has_explicit_credit_intent:
            transaction_type = "credit"

        is_expense_query = not has_explicit_credit_intent and effective_intent in (
            ExtractionIntent.SPENDING_TOTAL,
            ExtractionIntent.CATEGORY_BREAKDOWN,
            ExtractionIntent.BENEFICIARY_SUMMARY,
        )
        if not has_explicit_credit_intent and not is_expense_query and raw_lower:
            expense_cues = ("spending", "expense", "spent", "cost", "paid", "send", "sent", "transfer", "transferred")
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
    effective_intent: ExtractionIntent,
) -> Filters | None:
    if not extraction.filters:
        return None
    group_by = (extraction.aggregation.group_by or "").strip().lower() if extraction.aggregation else ""
    counterparty = normalize_counterparty_filter(extraction.filters.recipient)
    transaction_type = None
    if group_by not in {"transaction_type", "type"}:
        transaction_type = infer_transaction_type(
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


def normalize_counterparty_filter(recipient: str | None) -> str | None:
    normalized = " ".join((recipient or "").strip().split())
    if not normalized:
        return None
    lowered = normalized.casefold()
    if lowered in COUNTERPARTY_PLACEHOLDERS or lowered.startswith("unknown "):
        return None
    return normalized
