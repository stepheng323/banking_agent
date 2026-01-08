"""Format nodes for query flow - template-based response generation."""

from datetime import date, datetime
from typing import Any

from langchain_core.runnables import Runnable

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

    if len(result) > 30:
        result = result[:27].rsplit(" ", 1)[0] + "…"

    return result


def _format_amount(amount: float) -> str:
    """Format amount in Naira."""
    amount = abs(amount)  # Always show positive values
    if amount >= 1000:
        return f"₦{amount:,.0f}"
    return f"₦{amount:.0f}"


async def format_node(state: QueryState, llm: Runnable = None) -> dict[str, Any]:
    """Format QueryResult into user-facing response."""
    query_result = state.get("query_result")
    has_more = state.get("has_more", False)
    show_expanded = state.get("show_expanded", False)
    current_page = state.get("current_page", 0)

    if not query_result:
        return {
            "flow_state": "complete",
            "response": "No results to display.",
            "session_active": False,
        }

    response = _format_query_result(query_result, has_more, show_expanded, current_page)

    session_active = has_more or (query_result.items and len(query_result.items) > 0)

    return {
        "flow_state": "complete",
        "response": response,
        "session_active": session_active,
    }


def _format_query_result(
    result: QueryResult, has_more: bool, show_expanded: bool = False, current_page: int = 0
) -> str:
    """Format QueryResult to response string."""
    # For analytics/summary results, return summary directly (unless expanding to show transactions)
    if not show_expanded and result.summary_text and "|" not in result.summary_text:
        # Balance queries should show summary only
        if "Balance:" in result.summary_text or "Total balance" in result.summary_text:
            return result.summary_text

        # Analytics summaries start with emoji or specific patterns
        if (
            result.summary_text.startswith("💸")
            or "You spent" in result.summary_text
            or "Total:" in result.summary_text
            or "Your average" in result.summary_text
            or "You made" in result.summary_text
            or "Top Recipients" in result.summary_text
        ):
            return result.summary_text

    # Check if this is a multi-account balance query
    if result.summary_text and result.summary_text.startswith("accounts:"):
        # Parse metadata from summary: "accounts:2|total:₦X"
        parts = dict(p.split(":") for p in result.summary_text.split("|") if ":" in p)
        account_count = int(parts.get("accounts", 0))
        total = parts.get("total", "₦0")

        lines = ["💰 *Your Accounts*", ""]

        if result.items:
            for item in result.items:
                bank_name = item.description
                amount = _format_amount(item.amount)
                lines.append(f"{amount} — {bank_name}")

        lines.append("")
        lines.append(f"*Total: {total}*")

        return "\n".join(lines)

    # DEBUG: Log pagination details
    from shared.utils.logging import get_logger

    logger = get_logger(__name__)
    logger.info(
        "FORMAT_DEBUG",
        show_expanded=show_expanded,
        items_len=len(result.items) if result.items else 0,
        current_page=current_page,
    )

    # Handle no results case for transaction lists
    if not result.items:
        return "No matching transactions found for your search."

    lines = []

    account_count = 1
    pagination = ""
    heading = "📋 *Transactions*"

    # Check for dynamic heading from recipient drill-down or analytics
    if result.summary_text:
        # If summary contains recipient name pattern (*Name* — ₦X), use it as heading
        if "—" in result.summary_text and result.summary_text.startswith("*"):
            heading = f"📋 {result.summary_text.split(chr(10))[0]}"  # First line only
        elif "|" in result.summary_text:
            # Standard pagination info
            parts = dict(p.split(":") for p in result.summary_text.split("|") if ":" in p)
            account_count = int(parts.get("accounts", 1))
            showing = parts.get("showing", "")
            total = parts.get("total", "")
            if showing and total:
                pagination = f"Showing {showing} of {total}"

    if account_count > 1:
        heading = f"📋 *Transactions* _(across {account_count} accounts)_"

    lines.append(heading)
    lines.append("")

    if result.items:
        # Local pagination for extended items list (analytics drill-down)
        page_size = 5
        start_idx = current_page * page_size
        end_idx = start_idx + page_size

        display_items = result.items[start_idx:end_idx]
        remaining_count = len(result.items) - end_idx if end_idx < len(result.items) else 0

        # Update pagination display for local paging
        if show_expanded:
            total_items = len(result.items)
            current_showing = f"{start_idx + 1}-{min(end_idx, total_items)}"
            pagination = f"Showing {current_showing} of {total_items}"

        grouped = _group_items_by_date(display_items)
        for date_str, items in grouped.items():
            lines.append(f"*{date_str}*")
            for item in items:
                counterparty = item.metadata.get("counterparty") if item.metadata else None
                amount = _format_amount(item.amount)
                tx_type = item.metadata.get("type", "") if item.metadata else ""

                if counterparty:
                    if "transfer" in item.description.lower():
                        prefix = "Transfer from" if tx_type == "credit" else "Transfer to"
                        narration = f"{prefix} {counterparty}"
                    else:
                        narration = counterparty
                else:
                    narration = _humanize_narration(item.description)
                label = "Received" if tx_type == "credit" else "Sent"
                bank_name = item.metadata.get("bank_name", "") if item.metadata else ""

                if bank_name:
                    lines.append(f"{amount} • {label} — {narration} _({bank_name})_")
                else:
                    lines.append(f"{amount} • {label} — {narration}")
            lines.append("")

        if remaining_count > 0:
            lines.append(f"_{remaining_count} more transactions. Reply **Next** to continue._")
            lines.append("")

        if lines and lines[-1] == "":
            lines.pop()

    if pagination:
        lines.append("")
        lines.append(f"_{pagination}_")

    if has_more:
        lines.append("_*more* for next page_")

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
