"""Formatter for query responses."""

from datetime import date, datetime

from apps.core.src.agent.graphs.query.models import QueryResult, QueryResultItem
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class QueryFormatter:
    """Formatter for query execution results."""

    @staticmethod
    def format(
        result: QueryResult,
        current_page: int = 0,
        show_expanded: bool = False,
        has_more: bool = False,
    ) -> str:
        """Format QueryResult into user-facing response."""
        if not result:
            return "No results to display."

        response = QueryFormatter._format_query_result(result, has_more, show_expanded, current_page)
        return response

    @staticmethod
    def _format_date(d: date | str) -> str:
        """Format date to 'Dec 28' style."""
        if isinstance(d, str):
            try:
                d = datetime.strptime(d[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                return d[:10] if d else "Unknown"
        return d.strftime("%b %d").replace(" 0", " ")

    @staticmethod
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
            result = result[:27].rsplit(" ", 1)[0] + r"…"

        return result

    @staticmethod
    def _format_amount(amount: float) -> str:
        """Format amount in Naira."""
        amount = abs(amount)  # Always show positive values
        if amount >= 1000:
            return f"₦{amount:,.0f}"
        return f"₦{amount:.0f}"

    @staticmethod
    def _format_query_result(
        result: QueryResult,
        has_more: bool,
        show_expanded: bool = False,
        current_page: int = 0,
    ) -> str:
        """Format QueryResult to response string."""
        # For analytics/summary results, return summary directly (unless expanding to show transactions)
        if not show_expanded and result.summary_text and "|" not in result.summary_text:
            # Balance queries should show summary only
            if (
                "Balance:" in result.summary_text  # Legacy check
                or "balance" in result.summary_text.lower()
                or (result.items and result.items[0].metadata.get("type") == "balance")
            ):
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

        if (
            result.summary_text
            and result.summary_text.startswith("accounts:")
            and "showing:" not in result.summary_text
        ):
            parts = dict(p.split(":") for p in result.summary_text.split("|") if ":" in p)
            account_count = int(parts.get("accounts", 0))
            total = parts.get("total", "₦0")

            lines = ["💰 *Your Accounts*", ""]

            if result.items:
                for item in result.items:
                    bank_name = item.description
                    amount = QueryFormatter._format_amount(item.amount)
                    lines.append(f"{amount} — {bank_name}")

            lines.append("")
            lines.append(f"*Total: {total}*")

            return "\n".join(lines)

        if result.summary_text and result.summary_text.startswith("Breakdown by"):
            lines = [f"📊 *{result.summary_text}*", ""]

            if result.items:
                total_breakdown_amount = sum(item.amount for item in result.items)

                for item in result.items:
                    name = item.description.title()
                    amount = QueryFormatter._format_amount(item.amount)
                    count = item.metadata.get("count", 0) if item.metadata else 0

                    percentage = 0
                    if total_breakdown_amount > 0:
                        percentage = int((item.amount / total_breakdown_amount) * 100)

                    lines.append(f"{amount} — {name} ({percentage}%, {count} txns)")

            return "\n".join(lines)

        logger.info(
            "FORMAT_DEBUG",
            show_expanded=show_expanded,
            items_len=len(result.items) if result.items else 0,
            current_page=current_page,
        )

        # Handle no results case for transaction lists
        if not result.items:
            return "No matching transactions found for your search."

        # Special handling for single transaction - show detailed view
        if len(result.items) == 1:
            item = result.items[0]
            lines = ["*Transaction Details*", ""]

            amount_str = f"₦{item.amount:,.2f}"
            lines.append(f"*Amount:* {amount_str}")
            lines.append(f"*Description:* {item.description}")
            lines.append(f"*Date:* {item.date.strftime('%B %d, %Y') if item.date else 'Unknown'}")

            if item.metadata:
                tx_type = item.metadata.get("type", "")
                if tx_type:
                    direction = "Outgoing (Debit)" if tx_type == "debit" else "Incoming (Credit)"
                    lines.append(f"*Type:* {direction}")

                bank_name = item.metadata.get("bank_name", "")
                if bank_name:
                    lines.append(f"*Bank:* {bank_name}")

                transaction_type = item.metadata.get("transaction_type", "")
                if transaction_type:
                    lines.append(f"*Category:* {transaction_type.title()}")

                status = item.metadata.get("status", "")
                if status:
                    status_display = (
                        "✅ Successful"
                        if status.lower() in ("success", "completed", "successful")
                        else f"⏳ {status.title()}"
                    )
                    lines.append(f"*Status:* {status_display}")

            if item.id:
                lines.append(f"*Ref:* {item.id}")

            lines.append("")

            transaction_type = item.metadata.get("transaction_type", "") if item.metadata else ""
            if transaction_type == "transfer":
                lines.append("_Reply: 'receipt' for proof | 'issue' to report a problem_")

            return "\n".join(lines)

        lines = []

        account_count = 1
        pagination = ""
        heading = "*Transactions*"

        # Check for dynamic heading from recipient drill-down or analytics
        if result.summary_text:
            # If summary contains recipient name pattern (*Name* — ₦X), use it as heading
            if "—" in result.summary_text and result.summary_text.startswith("*"):
                heading = result.summary_text.split(chr(10))[0]  # First line only
            elif "|" in result.summary_text:
                # Standard pagination info
                parts = dict(p.split(":") for p in result.summary_text.split("|") if ":" in p)
                account_count = int(parts.get("accounts", 1))
                showing = parts.get("showing", "")
                total = parts.get("total", "")
                if showing and total:
                    pagination = f"Showing {showing} of {total}"

        if account_count > 1:
            heading = f"*Transactions* _(across {account_count} accounts)_"

        lines.append(heading)
        lines.append("")

        if result.items:
            # Local pagination for extended items list (analytics drill-down)
            page_size = 5

            # Only slice if we seem to have more items than a single page
            if len(result.items) > page_size:
                start_idx = current_page * page_size
                end_idx = start_idx + page_size
                display_items = result.items[start_idx:end_idx]
            else:
                display_items = result.items
                # If pre-paginated, start_idx for display purposes depends on page
                start_idx = current_page * page_size
                end_idx = start_idx + len(display_items)
            remaining_count = len(result.items) - end_idx if end_idx < len(result.items) else 0

            # Update pagination display for local paging
            if show_expanded:
                total_items = len(result.items)
                current_showing = f"{start_idx + 1}-{min(end_idx, total_items)}"
                pagination = f"Showing {current_showing} of {total_items}"

            grouped = QueryFormatter._group_items_by_date(display_items)
            for date_str, items in grouped.items():
                lines.append(f"*{date_str}*")
                for item in items:
                    counterparty = item.metadata.get("counterparty") if item.metadata else None
                    amount = QueryFormatter._format_amount(item.amount)
                    tx_type = item.metadata.get("type", "") if item.metadata else ""

                    real_type = item.metadata.get("transaction_type") if item.metadata else None

                    if real_type in ("airtime", "data"):
                        # Extract recipient from counterparty or description
                        recipient = counterparty
                        if not recipient:
                            # Try to extract phone number from narration
                            import re

                            phone_match = re.search(r"(\d{10,11})", item.description or "")
                            recipient = phone_match.group(1) if phone_match else "recipient"
                        narration = f"{real_type.title()} for {recipient}"
                    elif counterparty:
                        if "transfer" in item.description.lower():
                            prefix = "Transfer from" if tx_type == "credit" else "Transfer to"
                            narration = f"{prefix} {counterparty}"
                        else:
                            narration = counterparty
                    else:
                        narration = QueryFormatter._humanize_narration(item.description)
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

    @staticmethod
    def _group_items_by_date(
        items: list[QueryResultItem],
    ) -> dict[str, list[QueryResultItem]]:
        """Group items by date for display."""
        from collections import OrderedDict

        grouped: dict[str, list[QueryResultItem]] = OrderedDict()
        for item in items:
            date_key = QueryFormatter._format_date(item.date)
            if date_key not in grouped:
                grouped[date_key] = []
            grouped[date_key].append(item)
        return grouped
