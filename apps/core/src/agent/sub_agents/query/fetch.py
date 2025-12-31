"""Fetch and filter utilities for query execution."""

from dataclasses import asdict, is_dataclass
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
        result = [t for t in result if abs(t.get("amount", 0)) >= filters.min_amount]

    if filters.max_amount is not None:
        result = [t for t in result if abs(t.get("amount", 0)) <= filters.max_amount]

    if filters.transaction_type:
        result = [t for t in result if t.get("type") == filters.transaction_type]

    if filters.category:
        result = [t for t in result if match_category(t.get("narration", ""), filters.category)]

    if filters.merchant:
        result = [t for t in result if any(m.lower() in t.get("narration", "").lower() for m in filters.merchant)]

    if filters.exclude:
        result = [t for t in result if not any(e.lower() in t.get("narration", "").lower() for e in filters.exclude)]

    if filters.account_filter:
        filter_term = filters.account_filter.lower()
        result = [t for t in result if filter_term in t.get("bank_name", "").lower()]

    return result


async def fetch_and_filter(
    provider: BankingDataProvider,
    query: NormalizedQuery,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None = None,
) -> list[dict]:
    """Fetch transactions and apply filters."""
    if query.time_range:
        start = query.time_range.start.isoformat()
        end = query.time_range.end.isoformat()
    else:
        from datetime import timedelta

        end = date.today().isoformat()
        start = (date.today() - timedelta(days=7)).isoformat()

    bank_map: dict[str, str] = {}
    if accounts_info:
        for acc in accounts_info:
            acc_id = acc.get("account_id") or acc.get("mono_account_id", "")
            bank_name = acc.get("bank_name", "")
            if acc_id and bank_name:
                bank_map[acc_id] = bank_name

    def to_dict(t: Any) -> dict:
        if hasattr(t, "model_dump"):
            return t.model_dump()
        elif is_dataclass(t) and not isinstance(t, type):
            d = asdict(t)
            if "transaction_id" in d:
                d["id"] = d.pop("transaction_id")
            if "transaction_type" in d:
                d["type"] = d.pop("transaction_type")
            return d
        elif isinstance(t, dict):
            return t
        return {"raw": str(t)}

    if query.accounts_scope == "all" and len(account_ids) > 1:
        all_txns: list[dict[str, Any]] = []
        for acc_id in account_ids:
            txns = await provider.get_transactions(acc_id, start_date=start, end_date=end, limit=100)
            for t in txns:
                td = to_dict(t)
                td["bank_name"] = bank_map.get(acc_id, "")
                all_txns.append(td)
        transactions = sorted(all_txns, key=lambda t: t.get("date", ""), reverse=True)
    else:
        txns = await provider.get_transactions(account_id, start_date=start, end_date=end, limit=100)
        transactions = [to_dict(t) for t in txns]

    if query.filters:
        transactions = apply_filters(transactions, query.filters)

    return transactions
