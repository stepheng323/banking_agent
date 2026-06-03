"""Formatter for query responses."""

from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.models.domain import QueryResult
from banking.transactions.query.presentation.presentation_planner import build_presentation_plan


class QueryFormatter:
    """Formatter for query execution results."""

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

        plan = build_presentation_plan(
            result,
            locale=locale,
            current_page=current_page,
            show_expanded=show_expanded,
            has_more=has_more,
        )
        if plan is None:
            return render_message("query.session.completed", locale)

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
            return render_message("query.session.completed", locale)

        return "\n".join(lines)
