"""Aggregate nodes for query flow - data aggregation and transformation."""

from typing import Dict, Any
from collections import defaultdict

from apps.core.src.agent.sub_agents.query.graph.state import QueryState
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def aggregate_node(state: QueryState) -> Dict[str, Any]:
    """Aggregate transaction data based on query type."""
    query_type = state.get("query_type", "transaction_list")
    transactions = state.get("cached_transactions", [])
    page = state.get("current_page", 0)
    page_size = state.get("page_size", 10)
    
    # Paginate
    start_idx = page * page_size
    end_idx = start_idx + page_size
    page_transactions = transactions[start_idx:end_idx]
    
    if query_type in ("total_spent", "total_received"):
        result = _aggregate_totals(transactions, query_type)
    
    elif query_type in ("top_recipient", "top_sender"):
        result = _aggregate_top_counterparties(transactions, query_type)
    
    elif query_type == "breakdown":
        result = _aggregate_breakdown(transactions)
    
    else:  # transaction_list, search
        result = _aggregate_transaction_list(page_transactions, page, len(transactions))
    
    return {
        "flow_state": "formatting",
        "aggregated_result": result,
        "has_more": end_idx < len(transactions),
    }


def _aggregate_totals(transactions: list, query_type: str) -> Dict[str, Any]:
    """Aggregate total spent or received."""
    tx_type = "debit" if query_type == "total_spent" else "credit"
    filtered = [t for t in transactions if t.get("type") == tx_type]
    total_kobo = sum(t.get("amount", 0) for t in filtered)
    
    return {
        "type": "total",
        "total_naira": total_kobo / 100,
        "transaction_count": len(filtered),
        "transaction_type": tx_type,
    }


def _aggregate_top_counterparties(transactions: list, query_type: str) -> Dict[str, Any]:
    """Aggregate top recipients or senders."""
    tx_type = "debit" if query_type == "top_recipient" else "credit"
    filtered = [t for t in transactions if t.get("type") == tx_type]
    
    counterparty_totals = defaultdict(lambda: {"total": 0, "count": 0})
    for t in filtered:
        counterparty = _extract_counterparty(t.get("narration", "Unknown"))
        counterparty_totals[counterparty]["total"] += t.get("amount", 0)
        counterparty_totals[counterparty]["count"] += 1
    
    sorted_items = sorted(
        counterparty_totals.items(),
        key=lambda x: x[1]["total"],
        reverse=True
    )[:5]
    
    return {
        "type": "top_counterparties",
        "items": [
            {"name": name, "total_naira": data["total"] / 100, "count": data["count"]}
            for name, data in sorted_items
        ],
        "transaction_type": tx_type,
    }


def _aggregate_breakdown(transactions: list) -> Dict[str, Any]:
    """Aggregate daily breakdown."""
    daily_totals = defaultdict(lambda: {"debit": 0, "credit": 0, "count": 0})
    
    for t in transactions:
        date = t.get("date", "")[:10]
        tx_type = t.get("type", "unknown")
        amount = t.get("amount", 0)
        
        if tx_type in ("debit", "credit"):
            daily_totals[date][tx_type] += amount
            daily_totals[date]["count"] += 1
    
    sorted_days = sorted(daily_totals.items(), key=lambda x: x[0], reverse=True)[:7]
    
    return {
        "type": "breakdown",
        "days": [
            {
                "date": date,
                "spent_naira": data["debit"] / 100,
                "received_naira": data["credit"] / 100,
                "transaction_count": data["count"],
            }
            for date, data in sorted_days
        ],
        "total_spent_naira": sum(d["debit"] for _, d in sorted_days) / 100,
        "total_received_naira": sum(d["credit"] for _, d in sorted_days) / 100,
    }


def _aggregate_transaction_list(page_transactions: list, page: int, total: int) -> Dict[str, Any]:
    """Format transaction list for page."""
    return {
        "type": "transaction_list",
        "transactions": [
            {
                "date": t.get("date", "")[:10],
                "narration": t.get("narration", "Unknown"),
                "amount_naira": t.get("amount", 0) / 100,
                "type": t.get("type", "unknown"),
            }
            for t in page_transactions
        ],
        "page": page,
        "total": total,
    }


def _extract_counterparty(narration: str) -> str:
    """Extract counterparty name from narration."""
    if not narration:
        return "Unknown"
    
    narration = narration.strip()
    prefixes = ["Transfer to ", "Transfer from ", "Payment to ", "From ", "To "]
    for prefix in prefixes:
        if narration.startswith(prefix):
            narration = narration[len(prefix):]
            break
    
    if len(narration) > 30:
        narration = narration[:30] + "..."
    
    return narration
