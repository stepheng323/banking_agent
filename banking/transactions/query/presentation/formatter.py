"""Formatter for query responses."""

from __future__ import annotations

from typing import Any

from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.contracts import PresentationMode, PresentationPlan
from banking.transactions.query.models.domain import QueryResult, QueryResultItem
from banking.transactions.query.presentation.presentation_planner import build_presentation_plan
from shared.messaging.body_blocks import MessageDocument


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

    @staticmethod
    def format_blocks(
        result: QueryResult,
        current_page: int = 0,
        show_expanded: bool = False,
        has_more: bool = False,
        locale: str = "en",
    ) -> MessageDocument | None:
        """Format QueryResult into mobile-friendly structured body blocks."""
        if not result:
            return [{"type": "text", "text": render_message("query.format.no_results_display", locale)}]

        plan = build_presentation_plan(
            result,
            locale=locale,
            current_page=current_page,
            show_expanded=show_expanded,
            has_more=has_more,
        )
        if plan is None:
            return [{"type": "text", "text": render_message("query.session.completed", locale)}]

        if plan.mode == PresentationMode.TRANSACTION_LIST and result.items:
            return _transaction_list_blocks(result, plan, current_page=current_page, locale=locale)
        return _generic_plan_blocks(plan)


def _generic_plan_blocks(plan: PresentationPlan) -> MessageDocument | None:
    blocks: MessageDocument = []
    if plan.heading:
        blocks.append({"type": "heading", "text": _clean_visible_text(plan.heading)})
    if plan.lead_text:
        blocks.append({"type": "text", "text": _clean_visible_text(plan.lead_text)})
    for evidence_line in plan.evidence_lines:
        text = _clean_visible_text(evidence_line)
        if text:
            blocks.append({"type": "text", "text": text})

    if plan.items:
        items_text = "\n".join(plan.items).strip()
        if items_text:
            blocks.append({"type": "text", "text": items_text})

    if plan.hint_text:
        text = _clean_visible_text(plan.hint_text)
        if text:
            blocks.append({"type": "text", "text": text})
    return blocks or None


def _transaction_list_blocks(
    result: QueryResult,
    plan: PresentationPlan,
    *,
    current_page: int,
    locale: str,
) -> MessageDocument | None:
    return _generic_plan_blocks(plan)


def _paged_items(items: list[QueryResultItem], *, current_page: int) -> list[QueryResultItem]:
    page_size = 5
    if len(items) <= page_size:
        return items
    start_idx = max(current_page, 0) * page_size
    return items[start_idx : start_idx + page_size]


def _transaction_item_block_status(item: QueryResultItem) -> str | None:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    status = _normalize_status(
        metadata.get("display_status")
        or metadata.get("status")
        or metadata.get("local_status")
        or metadata.get("provider_status")
    )
    if status in {"", "success", "successful", "completed", "complete", "confirmed", "posted"}:
        return None
    if status in {"failed", "failure", "declined", "rejected"}:
        return "failed"
    if status in {"reversed", "refunded"}:
        return "failed"
    if status in {"pending", "processing", "queued", "in progress"}:
        return "processing"
    return None


def _normalize_status(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def _clean_visible_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for token in ("**", "*"):
        text = text.replace(token, "")
    return "\n".join(line.strip().strip("_").strip() for line in text.splitlines()).strip()
