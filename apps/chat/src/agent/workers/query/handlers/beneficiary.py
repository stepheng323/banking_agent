"""Beneficiary summary handler."""

from collections import defaultdict
from datetime import date
from typing import Any

from apps.chat.src.agent.workers.query.models.domain import (
    QueryExecutionContract,
    QueryResult,
    QueryResultItem,
)
from apps.chat.src.agent.workers.query.presentation.scope import build_beneficiary_summary_header
from apps.chat.src.agent.workers.query.services.fetching.fetch import extract_counterparty, fetch_and_filter
from shared.clients.abstractions.banking import BankDataProvider
from shared.i18n.renderer import render_message

_TRAILING_RECIPIENT_PUNCTUATION = ".,;:!?"


def _clean_recipient_display_name(name: str) -> str:
    """Normalize cosmetic recipient variants without fuzzy merging."""
    cleaned = " ".join(name.strip().split()).rstrip(_TRAILING_RECIPIENT_PUNCTUATION).strip()
    return cleaned or " ".join(name.strip().split())


def _normalize_recipient_key(name: str) -> str:
    return _clean_recipient_display_name(name).casefold()


async def handle_beneficiary_summary(
    provider: BankDataProvider,
    contract: QueryExecutionContract,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None = None,
    current_page: int = 0,
    page_size: int = 5,
    user_id: str | None = None,
    language: str = "en",
) -> QueryResult:
    """Handle beneficiary summary queries."""
    del current_page, page_size
    transactions = await fetch_and_filter(
        provider,
        contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
    )

    # Filter to actual transfers (exclude bank charges, fees, etc.)
    exclude_patterns = ("CHARGE", "FEE", "STAMP DUTY", "VAT", "SMS ALERT", "CARD MAINTENANCE", "COT", "NOTIFICATION")
    debits = [
        t
        for t in transactions
        if t.get("type") == "debit" and not any(pat in t.get("narration", "").upper() for pat in exclude_patterns)
    ]

    # Group by recipient with minimal normalization (case/space/trailing punctuation).
    counterparties: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"display_name": "", "total": 0, "count": 0, "transactions": []}
    )
    for t in debits:
        recipient_name = str(t.get("recipient_name") or t.get("counterparty") or "").strip()
        raw_name = recipient_name or extract_counterparty(t.get("narration", ""), locale=language)
        display_name = _clean_recipient_display_name(raw_name)
        key = _normalize_recipient_key(display_name)
        if not key:
            key = render_message("query.fetch.counterparty.unknown", language).casefold()
            display_name = render_message("query.fetch.counterparty.unknown", language)

        bucket = counterparties[key]
        if not bucket["display_name"]:
            bucket["display_name"] = display_name
        bucket["total"] += abs(t.get("amount", 0))
        bucket["count"] += 1
        bucket["transactions"].append(t)

    # Sort by count (frequency) or amount (total) based on query
    sort_key = contract.aggregation.sort_by if contract.aggregation and contract.aggregation.sort_by else "amount"
    if sort_key == "count":
        sorted_cp = sorted(
            counterparties.values(),
            key=lambda item: (-item["count"], -item["total"], str(item["display_name"]).casefold()),
        )
        heading_type = render_message("query.beneficiary.heading_most_frequent", language)
    else:
        sorted_cp = sorted(
            counterparties.values(),
            key=lambda item: (-item["total"], -item["count"], str(item["display_name"]).casefold()),
        )
        heading_type = render_message("query.beneficiary.heading_top", language)

    limit = contract.aggregation.limit if contract.aggregation else 5

    # Determine timeframe text
    if contract.time_range:
        start_str = contract.time_range.start.strftime("%b %d")
        end_str = contract.time_range.end.strftime("%b %d")
        timeframe = render_message(
            "query.beneficiary.timeframe_range",
            language,
            {"start": start_str, "end": end_str},
        )
    else:
        timeframe = render_message("query.beneficiary.timeframe_last_30_days", language)

    heading = build_beneficiary_summary_header(
        contract,
        timeframe=timeframe,
        ranking_heading=heading_type,
        locale=language,
    )
    items = []

    for i, data in enumerate(sorted_cp[:limit]):
        name = str(data["display_name"])
        total = abs(data["total"])
        count = data["count"]

        # Store transactions for drill-down
        items.append(
            QueryResultItem(
                id=str(i),
                description=name,
                amount=total,
                date=contract.time_range.end if contract.time_range else date.today(),
                metadata={"recipient_name": name, "count": count, "transactions": data["transactions"]},
            )
        )

    if not items:
        return QueryResult(summary_text=render_message("query.beneficiary.no_outgoing_transfers", language))

    return QueryResult(
        summary_text=heading,
        items=items,
    )
