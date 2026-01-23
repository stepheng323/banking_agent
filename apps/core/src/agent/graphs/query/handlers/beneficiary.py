"""Beneficiary summary handler."""

from collections import defaultdict
from datetime import date
from typing import Any

from apps.core.src.agent.graphs.query.fetch import extract_counterparty, fetch_and_filter
from apps.core.src.agent.graphs.query.models import NormalizedQuery, QueryResult, QueryResultItem
from shared.clients.abstractions.banking import BankingDataProvider


async def handle_beneficiary_summary(
    provider: BankingDataProvider,
    query: NormalizedQuery,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None = None,
    current_page: int = 0,
    page_size: int = 5,
    user_id: str | None = None,
) -> QueryResult:
    """Handle beneficiary summary queries."""
    transactions = await fetch_and_filter(
        provider, query, account_id, account_ids, accounts_info, user_id=user_id
    )

    # Filter to actual transfers (exclude bank charges, fees, etc.)
    exclude_patterns = ("CHARGE", "FEE", "STAMP DUTY", "VAT", "SMS ALERT", "CARD MAINTENANCE", "COT", "NOTIFICATION")
    debits = [
        t
        for t in transactions
        if t.get("type") == "debit" and not any(pat in t.get("narration", "").upper() for pat in exclude_patterns)
    ]

    # Group by counterparty
    counterparties: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0, "count": 0, "transactions": []})
    for t in debits:
        name = extract_counterparty(t.get("narration", ""))
        counterparties[name]["total"] += abs(t.get("amount", 0))
        counterparties[name]["count"] += 1
        counterparties[name]["transactions"].append(t)

    # Sort by count (frequency) or amount (total) based on query
    sort_key = query.aggregation.sort_by if query.aggregation and query.aggregation.sort_by else "amount"
    if sort_key == "count":
        sorted_cp = sorted(counterparties.items(), key=lambda x: x[1]["count"], reverse=True)
        heading_type = "Most Frequent"
    else:
        sorted_cp = sorted(counterparties.items(), key=lambda x: x[1]["total"], reverse=True)
        heading_type = "Top"

    limit = query.aggregation.limit if query.aggregation else 5

    # Determine timeframe text
    if query.time_range:
        start_str = query.time_range.start.strftime("%b %d")
        end_str = query.time_range.end.strftime("%b %d")
        timeframe = f"{start_str} – {end_str}"
    else:
        timeframe = "last 30 days"

    # Build response
    lines = [f"*{heading_type} Recipients* ({timeframe})\n"]
    items = []

    for i, (name, data) in enumerate(sorted_cp[:limit]):
        total = abs(data["total"]) / 100
        count = data["count"]
        lines.append(f"{name} • ₦{total:,.0f} ({count}x)")

        # Store transactions for drill-down
        items.append(
            QueryResultItem(
                id=str(i),
                description=name,
                amount=total,
                date=query.time_range.end if query.time_range else date.today(),
                metadata={"count": count, "transactions": data["transactions"]},
            )
        )

    if not items:
        return QueryResult(summary_text="No outgoing transfers found.")

    lines.append("")
    lines.append("_Reply with a name to see those transactions_")

    return QueryResult(
        summary_text="\n".join(lines),
        items=items,
    )
