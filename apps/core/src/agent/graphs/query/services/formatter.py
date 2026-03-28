"""Formatter for query responses."""

from datetime import timedelta
from typing import cast

from apps.core.src.agent.graphs.query.models import (
    NormalizedQuery,
    QueryAnswerStrategy,
    QueryIntent,
    QueryResult,
    TimeRange,
)
from apps.core.src.agent.graphs.query.services.answer_strategy import build_fact_no_results_text
from apps.core.src.agent.graphs.query.services.contracts import build_presentation_plan
from apps.core.src.agent.graphs.query.utils.timezone import lagos_today
from apps.core.src.agent.shared.query_contracts import PresentationMode
from shared.i18n import render_message
from shared.i18n.message_keys import MessageKey


class QueryFormatter:
    """Formatter for query execution results."""

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
    def _format_presentation_plan(
        result: QueryResult,
        *,
        locale: str,
        current_page: int = 0,
        show_expanded: bool = False,
        has_more: bool = False,
    ) -> str | None:
        plan = build_presentation_plan(
            result,
            locale=locale,
            current_page=current_page,
            show_expanded=show_expanded,
            has_more=has_more,
        )
        if plan is None:
            return None

        lines: list[str] = []
        if plan.heading:
            lines.append(plan.heading)
        if plan.lead_text:
            if lines:
                lines.append("")
            lines.append(plan.lead_text)
        if plan.evidence_lines:
            if lines:
                lines.append("")
            lines.extend(plan.evidence_lines)
        if plan.items:
            if lines:
                lines.append("")
            lines.extend(plan.items)
        if plan.hint_text:
            if lines:
                lines.append("")
            lines.append(plan.hint_text)

        if not lines:
            return None

        if plan.mode in {PresentationMode.DIRECT_ANSWER, PresentationMode.CLARIFY, PresentationMode.SUMMARY_LIST}:
            return "\n".join(lines)
        if not plan.items:
            return None
        return "\n".join(lines)

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
    def _format_query_result(
        result: QueryResult,
        has_more: bool,
        show_expanded: bool = False,
        current_page: int = 0,
        locale: str = "en",
    ) -> str:
        """Format QueryResult to response string."""
        rendered_plan = QueryFormatter._format_presentation_plan(
            result,
            locale=locale,
            current_page=current_page,
            show_expanded=show_expanded,
            has_more=has_more,
        )
        if rendered_plan is not None:
            return rendered_plan

        summary_parts = QueryFormatter._parse_summary_parts(result.summary_text)
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

            if QueryFormatter._has_balance_item(result):
                return result.summary_text

        # Handle no results case for transaction lists
        if not result.items:
            return QueryFormatter._format_no_results(result, locale)
        return render_message("query.session.completed", locale)
