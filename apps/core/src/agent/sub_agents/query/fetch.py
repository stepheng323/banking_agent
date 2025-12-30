"""Fetch and filter utilities for query execution."""

from datetime import date, datetime
from typing import Any

from apps.core.src.agent.sub_agents.query.models import Filters, NormalizedQuery, match_category
from shared.clients.abstractions.banking import BankingDataProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def parse_date(date_str: str) -> date:
    """Parse date string to date object."""
    if not date_str:
        return date.today()
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except ValueError:
        return date.today()


def extract_counterparty(narration: str) -> str:
    """Extract counterparty name from narration."""
    if not narration:
        return "Unknown"

    narration = narration.strip()
    prefixes = ["Transfer to ", "Transfer from ", "Payment to ", "From ", "To "]
    for prefix in prefixes:
        if narration.startswith(prefix):
            narration = narration[len(prefix) :]
            break

    if len(narration) > 25:
        narration = narration[:22] + "..."

    return narration


def apply_filters(transactions: list[dict], filters: Filters) -> list[dict]:
    """Apply filters to transaction list."""
    result = transactions

    if filters.min_amount is not None:
        result = [t for t in result if t.get("amount", 0) >= filters.min_amount * 100]

    if filters.max_amount is not None:
        result = [t for t in result if t.get("amount", 0) <= filters.max_amount * 100]

    if filters.transaction_type:
        result = [t for t in result if t.get("type") == filters.transaction_type]

    if filters.category:
        result = [t for t in result if match_category(t.get("narration", ""), filters.category)]

    if filters.merchant:
        result = [t for t in result if any(m.lower() in t.get("narration", "").lower() for m in filters.merchant)]

    if filters.exclude:
        result = [t for t in result if not any(e.lower() in t.get("narration", "").lower() for e in filters.exclude)]

    return result


async def fetch_and_filter(
    provider: BankingDataProvider,
    query: NormalizedQuery,
    account_id: str,
    account_ids: list[str],
) -> list[dict]:
    """Fetch transactions and apply filters."""
    start = query.time_range.start.isoformat() if query.time_range else None
    end = query.time_range.end.isoformat() if query.time_range else None

    if query.accounts_scope == "all" and len(account_ids) > 1:
        all_txns: list[dict[str, Any]] = []
        for acc_id in account_ids:
            txns = await provider.get_transactions(acc_id, start_date=start, end_date=end, limit=100)
            all_txns.extend([t.model_dump() if hasattr(t, "model_dump") else t for t in txns])
        transactions = sorted(all_txns, key=lambda t: t.get("date", ""), reverse=True)
    else:
        txns = await provider.get_transactions(account_id, start_date=start, end_date=end, limit=100)
        transactions = [t.model_dump() if hasattr(t, "model_dump") else t for t in txns]

    if query.filters:
        transactions = apply_filters(transactions, query.filters)

    return transactions
