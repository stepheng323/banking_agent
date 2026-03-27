"""Formatter for query responses."""

from datetime import date, datetime, timedelta
from typing import cast

from apps.core.src.agent.graphs.query.models import (
    NormalizedQuery,
    QueryAnswerStrategy,
    QueryIntent,
    QueryResult,
    QueryResultItem,
    SurfaceType,
    TimeRange,
)
from apps.core.src.agent.graphs.query.services.answer_strategy import (
    build_direct_fact_answer,
    build_fact_no_results_text,
)
from apps.core.src.agent.graphs.query.utils.timezone import lagos_today
from shared.i18n import render_message
from shared.i18n.message_keys import MessageKey
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class QueryFormatter:
    """Formatter for query execution results."""

    @staticmethod
    def _breakdown_group_by(result: QueryResult) -> str | None:
        surface = result.surface
        if surface is None or surface.context is None:
            return None
        return cast(str | None, surface.context.get("group_by"))

    @staticmethod
    def _has_search_shaped_no_results_context(query_snapshot: NormalizedQuery | None) -> bool:
        """Return whether no-results wording should stay generic/search-oriented."""
        if not query_snapshot:
            return False
        if query_snapshot.aggregation is not None:
            return True

        filters = query_snapshot.filters
        if not filters:
            return False

        return any(
            (
                bool(filters.category),
                bool(filters.merchant),
                bool(filters.counterparty),
                filters.min_amount is not None,
                filters.max_amount is not None,
                bool(filters.exclude),
                filters.account_filter is not None,
                query_snapshot.account_name is not None,
            )
        )

    @staticmethod
    def _format_factual_no_results(
        time_range: TimeRange,
        locale: str,
        transaction_type: str | None,
    ) -> str:
        """Format direct factual no-results copy for plain time-scoped transaction lookups."""
        today = lagos_today()
        yesterday = today - timedelta(days=1)

        if time_range.start == time_range.end == today:
            suffix = "today"
        elif time_range.start == time_range.end == yesterday:
            suffix = "yesterday"
        else:
            suffix = "period"

        if transaction_type in ("credit", "debit"):
            key = cast(MessageKey, f"query.format.no_transactions_with_type_{suffix}")
            return render_message(
                key,
                locale,
                {"transaction_type": transaction_type},
            )

        key = cast(MessageKey, f"query.format.no_transactions_{suffix}")
        return render_message(key, locale)

    @staticmethod
    def _parse_summary_parts(summary_text: str | None) -> dict[str, str]:
        """Parse pipe-delimited summary parts into a dictionary."""
        if not summary_text or "|" not in summary_text:
            return {}

        parts: dict[str, str] = {}
        for part in summary_text.split("|"):
            if ":" not in part:
                continue
            key, value = part.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key:
                parts[key] = value
        return parts

    @staticmethod
    def _has_balance_item(result: QueryResult) -> bool:
        """Check whether any result item is a balance entry."""
        if not result.items:
            return False
        return any(item.metadata and item.metadata.get("type") == "balance" for item in result.items)

    @staticmethod
    def _has_rank_metadata(result: QueryResult) -> bool:
        """Check if ranking metadata exists on any item."""
        if not result.items:
            return False
        return any(item.metadata and item.metadata.get("rank") for item in result.items)

    @staticmethod
    def format(
        result: QueryResult,
        current_page: int = 0,
        show_expanded: bool = False,
        has_more: bool = False,
        locale: str = "en",
    ) -> str:
        """Format QueryResult into user-facing response."""
        if not result:
            return render_message("query.format.no_results_display", locale)

        response = QueryFormatter._format_query_result(result, has_more, show_expanded, current_page, locale)
        return response

    @staticmethod
    def _format_date(d: date | str, locale: str = "en") -> str:
        """Format date to 'Dec 28' style."""
        if isinstance(d, str):
            raw_date = d
            try:
                d = datetime.strptime(d[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                return raw_date[:10] if raw_date else render_message("query.format.unknown", locale)
        return d.strftime("%b %d").replace(" 0", " ")

    @staticmethod
    def _humanize_narration(narration: str, locale: str = "en") -> str:
        """Clean up narration for display."""
        if not narration:
            return render_message("query.format.narration.transaction", locale)

        narration = narration.strip()

        if "NIP TRANSFER" in narration.upper() or narration.startswith("0000"):
            parts = narration.split()
            for i, part in enumerate(parts):
                if part.upper() in ("TO", "FROM") and i + 1 < len(parts):
                    name_parts = parts[i + 1 :]
                    name = " ".join(name_parts).title()
                    prefix = (
                        render_message("query.format.narration.transfer_to", locale)
                        if part.upper() == "TO"
                        else render_message("query.format.narration.transfer_from", locale)
                    )
                    return f"{prefix} {name[:25]}"
            return render_message("query.format.narration.bank_transfer", locale)

        if narration.upper().startswith("POS PURCHASE"):
            merchant = narration[14:].strip(" -")
            return merchant.title()[:30] if merchant else render_message("query.format.narration.pos_purchase", locale)

        replacements = [
            ("TRANSFER TO ", render_message("query.format.narration.transfer_to", locale) + " "),
            ("TRANSFER FROM ", render_message("query.format.narration.transfer_from", locale) + " "),
            ("ATM WITHDRAWAL", render_message("query.format.narration.atm_withdrawal", locale)),
            ("AIRTIME PURCHASE", render_message("query.format.narration.airtime", locale)),
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
    def _format_no_results(result: QueryResult, locale: str) -> str:
        """Format no-results output using available query context."""
        query_snapshot = result.query_snapshot
        time_range = query_snapshot.time_range if query_snapshot else None
        tx_type = query_snapshot.filters.transaction_type if query_snapshot and query_snapshot.filters else None
        if (
            query_snapshot
            and query_snapshot.intent in {QueryIntent.TRANSACTION_LIST, QueryIntent.TRANSACTION_SEARCH}
            and time_range is not None
            and not QueryFormatter._has_search_shaped_no_results_context(query_snapshot)
        ):
            return QueryFormatter._format_factual_no_results(time_range, locale, tx_type)

        if tx_type not in ("credit", "debit"):
            return render_message("query.format.no_matching_transactions", locale)

        time_suffix = ""
        if time_range:
            if time_range.start == time_range.end == lagos_today():
                time_suffix = render_message("query.format.no_results_time_suffix_today", locale)
            else:
                time_suffix = render_message("query.format.no_results_time_suffix_period", locale)

        return render_message(
            "query.format.no_results_with_type",
            locale,
            {"transaction_type": tx_type, "time_suffix": time_suffix},
        )

    @staticmethod
    def _month_start_and_end(d: date) -> tuple[date, date]:
        start = d.replace(day=1)
        next_month_anchor = (start + timedelta(days=32)).replace(day=1)
        end = next_month_anchor - timedelta(days=1)
        return start, end

    @staticmethod
    def _period_label(time_range: TimeRange | None, locale: str) -> str | None:
        if not time_range:
            return None

        today = lagos_today()
        if time_range.start == time_range.end == today:
            return render_message("query.format.heading_period_today", locale)

        this_month_start, _ = QueryFormatter._month_start_and_end(today)
        if time_range.start == this_month_start and time_range.end == today:
            return render_message("query.format.heading_period_this_month", locale)

        prev_month_end = this_month_start - timedelta(days=1)
        prev_month_start, _ = QueryFormatter._month_start_and_end(prev_month_end)
        if time_range.start == prev_month_start and time_range.end == prev_month_end:
            return prev_month_start.strftime("%B")

        return render_message(
            "query.format.heading_period_range",
            locale,
            {
                "start": QueryFormatter._format_date(time_range.start, locale),
                "end": QueryFormatter._format_date(time_range.end, locale),
            },
        )

    @staticmethod
    def _build_contextual_heading(query_snapshot: NormalizedQuery | None, locale: str) -> tuple[str, bool]:
        base_heading = render_message("query.format.heading_transactions_default", locale)
        if not query_snapshot or query_snapshot.intent not in {
            QueryIntent.TRANSACTION_LIST,
            QueryIntent.TRANSACTION_SEARCH,
        }:
            return base_heading, False

        filters = query_snapshot.filters
        heading = base_heading
        is_contextual = False

        tx_type = filters.transaction_type if filters else None
        categories = filters.category if filters and filters.category else []
        if len(categories) == 1 and tx_type == "debit":
            category_name = categories[0].strip().title()
            heading = render_message("query.format.heading_spending_category", locale, {"category": category_name})
            is_contextual = True
        elif tx_type in ("credit", "debit"):
            heading = render_message(
                "query.format.heading_transactions_type",
                locale,
                {
                    "transaction_type": render_message(
                        "query.format.heading_type_credit" if tx_type == "credit" else "query.format.heading_type_debit",
                        locale,
                    )
                },
            )
            is_contextual = True
        elif len(categories) == 1:
            category_name = categories[0].strip().title()
            heading = render_message("query.format.heading_transactions_category", locale, {"category": category_name})
            is_contextual = True

        account_filter = (filters.account_filter or "").strip() if filters else ""
        if account_filter:
            heading = render_message(
                "query.format.heading_suffix_account",
                locale,
                {"heading": heading, "account": account_filter},
            )
            is_contextual = True

        period_label = QueryFormatter._period_label(query_snapshot.time_range, locale)
        if period_label:
            heading = render_message(
                "query.format.heading_suffix_period",
                locale,
                {"heading": heading, "period": period_label},
            )
            is_contextual = True

        return heading, is_contextual

    @staticmethod
    def _format_query_result(
        result: QueryResult,
        has_more: bool,
        show_expanded: bool = False,
        current_page: int = 0,
        locale: str = "en",
    ) -> str:
        """Format QueryResult to response string."""
        summary_parts = QueryFormatter._parse_summary_parts(result.summary_text)
        surface_type = result.surface.type if result.surface is not None else None
        answer_strategy = result.answer_strategy

        if answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER:
            if result.answer_context is not None:
                lines = [result.answer_context.primary_text]
                if result.answer_context.secondary_text:
                    lines.extend(["", result.answer_context.secondary_text])
                if result.answer_context.hint_text:
                    lines.extend(["", result.answer_context.hint_text])
                return "\n".join(lines)
            if not result.items:
                fact_no_results = build_fact_no_results_text(result.query_snapshot)
                if fact_no_results:
                    return fact_no_results
                if result.summary_text:
                    return result.summary_text
                return QueryFormatter._format_no_results(result, locale)

        if answer_strategy == QueryAnswerStrategy.CLARIFY and result.answer_context is not None:
            return result.answer_context.primary_text

        if not show_expanded and result.summary_text and not summary_parts:
            if not result.items:
                return result.summary_text

            if result.query_snapshot and result.query_snapshot.intent in {
                QueryIntent.AFFORDABILITY,
                QueryIntent.TIME_COMPARISON,
            }:
                return result.summary_text

            if result.surface and result.surface.type == SurfaceType.SUMMARY:
                return result.summary_text

            if QueryFormatter._has_balance_item(result):
                return result.summary_text

        if summary_parts and "accounts" in summary_parts and "showing" not in summary_parts:
            parts = summary_parts
            account_count = int(parts.get("accounts", 0))
            total = parts.get("total", "₦0")

            lines = [render_message("query.format.accounts_header", locale), ""]

            if result.items:
                for item in result.items:
                    bank_name = item.description
                    amount = QueryFormatter._format_amount(item.amount)
                    lines.append(
                        render_message(
                            "query.format.accounts_item",
                            locale,
                            {"amount": amount, "bank_name": bank_name},
                        )
                    )

            lines.append("")
            lines.append(render_message("query.format.total_line", locale, {"total": total}))

            return "\n".join(lines)

        if result.summary_text and result.surface and result.surface.type == SurfaceType.BREAKDOWN:
            lines = [render_message("query.format.breakdown_heading", locale, {"summary": result.summary_text}), ""]
            breakdown_group_by = QueryFormatter._breakdown_group_by(result)

            total_abs = 0.0
            if result.items:
                total_abs = float(sum((abs(item.amount) for item in result.items), 0.0))

                for item in result.items:
                    name = item.description if breakdown_group_by == "account" else item.description.replace("_", " ").title()
                    amount = QueryFormatter._format_amount(item.amount)
                    count = item.metadata.get("count", 0) if item.metadata else 0

                    percentage_str = "0%"
                    if total_abs > 0:
                        pct = (abs(item.amount) / total_abs) * 100
                        percentage_str = "<1%" if 0 < pct < 1 else f"{int(pct)}%"

                    lines.append(
                        render_message(
                            "query.format.breakdown_item",
                            locale,
                            {
                                "amount": amount,
                                "name": name,
                                "percentage": percentage_str,
                                "count": count,
                            },
                        )
                    )

            lines.append("")
            lines.append(render_message("query.format.total_line", locale, {"total": f"₦{total_abs:,.0f}"}))
            return "\n".join(lines)

        # Trigger ranked list view via structured surface or explicit rank metadata.
        is_ranked_surface = (
            result.surface
            and result.surface.type == SurfaceType.LIST
            and isinstance(result.surface.context, dict)
            and result.surface.context.get("type") in ("largest", "smallest")
        )
        has_rank_metadata = QueryFormatter._has_rank_metadata(result)

        if result.summary_text and (is_ranked_surface or has_rank_metadata):
            # Bypass list rendering if surface is explicitly SINGLE_ITEM (e.g. "Highest expense")
            if result.surface and result.surface.type == SurfaceType.SINGLE_ITEM:
                pass
            else:
                heading = (
                    result.summary_text
                    if result.summary_text.startswith("🏆")
                    else render_message(
                        "query.format.ranked_heading",
                        locale,
                        {"summary": result.summary_text},
                    )
                )
                lines = [heading, ""]

                for i, item in enumerate(result.items or []):
                    amount = QueryFormatter._format_amount(item.amount)
                    date_str = QueryFormatter._format_date(item.date, locale)
                    name = item.description

                    # Use absolute rank if available (from backend pagination), else relative
                    rank = i + 1
                    if item.metadata and item.metadata.get("rank"):
                        rank = item.metadata["rank"]

                    # Check for bank name in metadata
                    bank_suffix = ""
                    if item.metadata and item.metadata.get("bank_name"):
                        bank_suffix = f" _({item.metadata['bank_name']})_"

                    lines.append(
                        render_message(
                            "query.format.ranked_item",
                            locale,
                            {
                                "rank": rank,
                                "amount": amount,
                                "name": name,
                                "date": date_str,
                                "bank_suffix": bank_suffix,
                            },
                        )
                    )

                return "\n".join(lines)

        logger.info(
            "FORMAT_DEBUG",
            show_expanded=show_expanded,
            items_len=len(result.items) if result.items else 0,
            current_page=current_page,
        )

        # Handle no results case for transaction lists
        if not result.items:
            return QueryFormatter._format_no_results(result, locale)

        # Special handling for single transaction - show detailed view
        if len(result.items) == 1 and surface_type == SurfaceType.SINGLE_ITEM:
            item = result.items[0]
            title = render_message("query.format.transaction_details_title", locale)
            if result.query_snapshot and result.query_snapshot.result_reference == "latest":
                title = render_message("query.format.last_transaction_title", locale)
                tx_filters = result.query_snapshot.filters
                if tx_filters and tx_filters.transaction_type in ("debit", "credit"):
                    title = render_message(
                        "query.format.last_transaction_type_title",
                        locale,
                        {"transaction_type": tx_filters.transaction_type},
                    )

            lines = []
            query_snapshot = result.query_snapshot
            if query_snapshot is not None and query_snapshot.answer_fact_field is not None:
                answer_context = build_direct_fact_answer(
                    item,
                    query=query_snapshot,
                    fact_field=query_snapshot.answer_fact_field,
                    locale=locale,
                )
                lines.extend([answer_context.primary_text, ""])

            lines.extend([f"*{title}*", ""])

            amount_str = f"₦{item.amount:,.2f}"
            lines.append(render_message("query.format.field_amount", locale, {"amount": amount_str}))
            lines.append(render_message("query.format.field_description", locale, {"description": item.description}))
            lines.append(
                render_message(
                    "query.format.field_date",
                    locale,
                    {
                        "date": item.date.strftime("%B %d, %Y")
                        if item.date
                        else render_message("query.format.unknown", locale),
                    },
                )
            )

            if item.metadata:
                tx_type = item.metadata.get("type", "")
                if tx_type:
                    direction = (
                        render_message("query.format.type_outgoing_debit", locale)
                        if tx_type == "debit"
                        else render_message("query.format.type_incoming_credit", locale)
                    )
                    lines.append(render_message("query.format.field_type", locale, {"type": direction}))

                bank_name = item.metadata.get("bank_name", "")
                if bank_name:
                    lines.append(render_message("query.format.field_bank", locale, {"bank_name": bank_name}))

                transaction_type = item.metadata.get("transaction_type", "")
                if transaction_type:
                    lines.append(
                        render_message(
                            "query.format.field_category",
                            locale,
                            {"category": transaction_type.title()},
                        )
                    )

                status = item.metadata.get("status", "")
                if status:
                    status_display = (
                        render_message("query.format.status_success", locale)
                        if status.lower() in ("success", "completed", "successful")
                        else render_message("query.format.status_pending_generic", locale, {"status": status.title()})
                    )
                    lines.append(render_message("query.format.field_status", locale, {"status": status_display}))

            if item.id:
                lines.append(render_message("query.format.field_ref", locale, {"reference": item.id}))

            lines.append("")

            transaction_type = item.metadata.get("transaction_type", "") if item.metadata else ""
            if transaction_type == "transfer":
                lines.append(render_message("query.format.transfer_reply_hint", locale))

            return "\n".join(lines)

        lines = []

        account_count = 1
        pagination = ""
        heading = render_message("query.format.heading_transactions_default", locale)
        is_recipient_heading = False
        contextual_heading_applied = False

        # Check for dynamic heading from recipient drill-down or analytics
        if result.summary_text:
            # If summary contains recipient name pattern (*Name* — ₦X), use it as heading
            if "—" in result.summary_text and result.summary_text.startswith("*"):
                heading = result.summary_text.split(chr(10))[0]  # First line only
                is_recipient_heading = True
            elif summary_parts:
                # Standard pagination info
                parts = summary_parts
                account_count = int(parts.get("accounts", 1))
                showing = parts.get("showing", "")
                total = parts.get("total", "")
                if showing and total:
                    pagination = render_message(
                        "query.format.pagination_showing",
                        locale,
                        {"showing": showing, "total": total},
                    )

        if not is_recipient_heading:
            heading, contextual_heading_applied = QueryFormatter._build_contextual_heading(result.query_snapshot, locale)

        if account_count > 1 and not contextual_heading_applied and not is_recipient_heading:
            heading = render_message(
                "query.format.transactions_across_accounts",
                locale,
                {"account_count": account_count},
            )

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
                pagination = render_message(
                    "query.format.pagination_showing",
                    locale,
                    {"showing": current_showing, "total": total_items},
                )

            grouped = QueryFormatter._group_items_by_date(display_items, locale)
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
                            recipient = (
                                phone_match.group(1)
                                if phone_match
                                else render_message("query.format.recipient_fallback", locale)
                            )
                        narration = render_message(
                            "query.format.narration.type_for_recipient",
                            locale,
                            {"type": real_type.title(), "recipient": recipient},
                        )
                    elif counterparty:
                        if "transfer" in item.description.lower():
                            prefix = (
                                render_message("query.format.narration.transfer_from", locale)
                                if tx_type == "credit"
                                else render_message("query.format.narration.transfer_to", locale)
                            )
                            narration = f"{prefix} {counterparty}"
                        else:
                            narration = counterparty
                    else:
                        narration = QueryFormatter._humanize_narration(item.description, locale)
                    label = (
                        render_message("query.format.label_received", locale)
                        if tx_type == "credit"
                        else render_message("query.format.label_sent", locale)
                    )
                    bank_name = item.metadata.get("bank_name", "") if item.metadata else ""

                    if bank_name:
                        lines.append(
                            render_message(
                                "query.format.transaction_item_with_bank",
                                locale,
                                {
                                    "amount": amount,
                                    "label": label,
                                    "narration": narration,
                                    "bank_name": bank_name,
                                },
                            )
                        )
                    else:
                        lines.append(
                            render_message(
                                "query.format.transaction_item",
                                locale,
                                {"amount": amount, "label": label, "narration": narration},
                            )
                        )
                lines.append("")

            if remaining_count > 0:
                lines.append(render_message("query.format.remaining_transactions", locale, {"count": remaining_count}))
                lines.append("")

            if lines and lines[-1] == "":
                lines.pop()

        if pagination:
            lines.append("")
            lines.append(f"_{pagination}_")

        if has_more:
            lines.append(render_message("query.format.more_for_next_page", locale))

        return "\n".join(lines) if lines else render_message("query.session.completed", locale)

    @staticmethod
    def _group_items_by_date(
        items: list[QueryResultItem],
        locale: str = "en",
    ) -> dict[str, list[QueryResultItem]]:
        """Group items by date for display."""
        from collections import OrderedDict

        grouped: dict[str, list[QueryResultItem]] = OrderedDict()
        for item in items:
            date_key = QueryFormatter._format_date(item.date, locale)
            if date_key not in grouped:
                grouped[date_key] = []
            grouped[date_key].append(item)
        return grouped
