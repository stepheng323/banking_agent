"""Format nodes for query flow - template-based response generation."""

from datetime import date, datetime
from typing import Any

from apps.core.src.agent.sub_agents.query.graph.state import QueryState
from apps.core.src.agent.sub_agents.query.models import QueryResult, QueryResultItem
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _format_date(d: date | str) -> str:
    """Format date to 'Dec 28' style."""
    if isinstance(d, str):
        try:
            d = datetime.strptime(d[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return d[:10] if d else "Unknown"
    return d.strftime("%b %d").replace(" 0", " ")


def _humanize_narration(narration: str) -> str:
    """Clean up narration for display."""
    if not narration:
        return "Transaction"

    narration = narration.strip()

    if "NIP TRANSFER" in narration.upper() or narration.startswith("0000"):
        parts = narration.split()
        for i, part in enumerate(parts):
            if part.upper() in ("TO", "FROM") and i + 1 < len(parts):
                name_parts = parts[i + 1 :]
                name = " ".join(name_parts).title()
                prefix = "Transfer to" if part.upper() == "TO" else "Transfer from"
                return f"{prefix} {name[:25]}"
        return "Bank Transfer"

    if narration.upper().startswith("POS PURCHASE"):
        merchant = narration[14:].strip(" -")
        return merchant.title()[:30] if merchant else "POS Purchase"

    replacements = [
        ("TRANSFER TO ", "Transfer to "),
        ("TRANSFER FROM ", "Transfer from "),
        ("ATM WITHDRAWAL", "ATM Withdrawal"),
        ("AIRTIME PURCHASE", "Airtime"),
    ]
    result = narration
    for old, new in replacements:
        if result.upper().startswith(old):
            result = new + result[len(old) :].title()
            break

    if len(result) > 35:
        result = result[:32] + "..."

    return result


def _format_amount(amount: float) -> str:
    """Format amount in Naira."""
    if amount >= 1000:
        return f"₦{amount:,.0f}"
    return f"₦{amount:.0f}"


async def format_node(state: QueryState, llm: Any = None) -> dict[str, Any]:
    """Format QueryResult into user-facing response."""
    query_result = state.get("query_result")
    has_more = state.get("has_more", False)

    if not query_result:
        return {
            "flow_state": "complete",
            "response": "No results to display.",
            "session_active": False,
        }

    response = _format_query_result(query_result, has_more)

    return {
        "flow_state": "complete",
        "response": response,
        "session_active": has_more,
    }


def _format_query_result(result: QueryResult, has_more: bool) -> str:
    """Format QueryResult to response string."""
    lines = []

    if result.summary_text:
        lines.append(f"*{result.summary_text}*")
        lines.append("")

    if result.items:
        grouped = _group_items_by_date(result.items)
        for date_str, items in grouped.items():
            lines.append(f"*{date_str}*")
            for item in items:
                narration = _humanize_narration(item.description)
                amount = _format_amount(item.amount)
                lines.append(f"• {narration} — {amount}")
            lines.append("")

        if lines and lines[-1] == "":
            lines.pop()

    if has_more:
        lines.append("")
        lines.append("_Reply *show more* to see earlier activity._")

    return "\n".join(lines) if lines else "Query completed."


def _group_items_by_date(items: list[QueryResultItem]) -> dict[str, list[QueryResultItem]]:
    """Group items by date for display."""
    from collections import OrderedDict

    grouped: dict[str, list[QueryResultItem]] = OrderedDict()
    for item in items:
        date_key = _format_date(item.date)
        if date_key not in grouped:
            grouped[date_key] = []
        grouped[date_key].append(item)
    return grouped
