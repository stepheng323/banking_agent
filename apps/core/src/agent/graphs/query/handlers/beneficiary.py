"""Beneficiary summary handler."""

from collections import defaultdict
from datetime import date
from typing import Any

from apps.core.src.agent.graphs.query.models import (
    QueryExecutionContract,
    QueryResult,
    QueryResultItem,
    ResultSurface,
    SurfaceType,
)
from apps.core.src.agent.graphs.query.services.fetch import extract_counterparty, fetch_and_filter
from shared.clients.abstractions.banking import BankDataProvider
from shared.i18n import render_message

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
    query = contract.normalized_query
    transactions = await fetch_and_filter(
        provider,
        query,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
        language=language,
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
        recipient_name = str(t.get("recipient_name") or "").strip()
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
    sort_key = query.aggregation.sort_by if query.aggregation and query.aggregation.sort_by else "amount"
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

    limit = query.aggregation.limit if query.aggregation else 5

    # Determine timeframe text
    if query.time_range:
        start_str = query.time_range.start.strftime("%b %d")
        end_str = query.time_range.end.strftime("%b %d")
        timeframe = render_message(
            "query.beneficiary.timeframe_range",
            language,
            {"start": start_str, "end": end_str},
        )
    else:
        timeframe = render_message("query.beneficiary.timeframe_last_30_days", language)

    # Build response
    lines = [
        render_message(
            "query.beneficiary.summary_header",
            language,
            {"heading_type": heading_type, "timeframe": timeframe},
        ),
        "",
    ]
    items = []

    for i, data in enumerate(sorted_cp[:limit]):
        name = str(data["display_name"])
        total = abs(data["total"])
        count = data["count"]
        lines.append(
            render_message(
                "query.beneficiary.summary_line",
                language,
                {"name": name, "total": f"{total:,.0f}", "count": count},
            )
        )

        # Store transactions for drill-down
        items.append(
            QueryResultItem(
                id=str(i),
                description=name,
                amount=total,
                date=query.time_range.end if query.time_range else date.today(),
                metadata={"recipient_name": name, "count": count, "transactions": data["transactions"]},
            )
        )

    if not items:
        return QueryResult(summary_text=render_message("query.beneficiary.no_outgoing_transfers", language))

    lines.extend(["", render_message("query.beneficiary.reply_name_hint", language)])

    return QueryResult(
        summary_text="\n".join(lines),
        items=items,
        surface=ResultSurface(type=SurfaceType.SUMMARY, items=[], context={"view": "beneficiary_summary"}),
    )
