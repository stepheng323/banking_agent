"""Formatter for query responses."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.contracts import PresentationMode, PresentationPlan
from banking.transactions.query.models.domain import QueryResult, QueryResultItem
from banking.transactions.query.presentation.formatting import format_query_amount, format_query_date
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
    for item in plan.items:
        text = _clean_visible_text(item)
        if text:
            blocks.append({"type": "text", "text": text})
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
    blocks: MessageDocument = []
    if plan.heading:
        blocks.append({"type": "heading", "text": _clean_visible_text(plan.heading)})

    display_items = _paged_items(result.items or [], current_page=current_page)
    grouped = _group_items_by_date(display_items, locale=locale)
    for date_label, items in grouped.items():
        blocks.append({"type": "heading", "text": date_label})
        for item in items:
            blocks.append({"type": "text", "text": _transaction_item_text(item, locale=locale)})

    if plan.hint_text:
        text = _clean_visible_text(plan.hint_text)
        if text:
            blocks.append({"type": "text", "text": text})

    return blocks or _generic_plan_blocks(plan)


def _paged_items(items: list[QueryResultItem], *, current_page: int) -> list[QueryResultItem]:
    page_size = 5
    if len(items) <= page_size:
        return items
    start_idx = max(current_page, 0) * page_size
    return items[start_idx : start_idx + page_size]


def _group_items_by_date(
    items: list[QueryResultItem],
    *,
    locale: str,
) -> OrderedDict[str, list[QueryResultItem]]:
    grouped: OrderedDict[str, list[QueryResultItem]] = OrderedDict()
    for item in items:
        date_key = format_query_date(item.date, locale=locale)
        grouped.setdefault(date_key, []).append(item)
    return grouped


def _transaction_item_text(item: QueryResultItem, *, locale: str) -> str:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    amount = format_query_amount(item.amount or 0.0)
    label = _transaction_item_label(item, metadata=metadata)
    detail = _transaction_item_detail(metadata)
    lines = [f"{amount} • {label}" if amount and label else amount or label]
    if detail:
        lines.append(detail)
    return "\n".join(line for line in lines if line)


def _transaction_item_label(item: QueryResultItem, *, metadata: dict[str, Any]) -> str:
    status_label = _status_label(item, metadata)
    counterparty = _first_text(
        metadata,
        "counterparty",
        "recipient_name",
        "recipient_resolved_name",
        "sender_name",
        "merchant",
    )
    description = str(item.description or "").strip()
    transaction_type = _first_text(metadata, "transaction_type").lower()
    direction = _first_text(metadata, "type").lower()
    target = counterparty or description or "transaction"

    if status_label:
        return f"{status_label} — {target}"
    if transaction_type in {"airtime", "data"}:
        return f"{transaction_type.title()} for {target}"
    if direction == "credit":
        return f"Received from {target}"
    return f"Sent to {target}"


def _transaction_item_detail(metadata: dict[str, Any]) -> str:
    bank_name = _first_text(metadata, "bank_name", "recipient_bank_name", "source_bank_name")
    account_number = _first_text(metadata, "account_number", "recipient_account", "source_account_number")
    if bank_name and account_number:
        return f"{bank_name} • {_mask_account(account_number)}"
    return bank_name


def _status_label(item: QueryResultItem, metadata: dict[str, Any]) -> str | None:
    status = _normalize_status(
        metadata.get("display_status")
        or metadata.get("status")
        or metadata.get("local_status")
        or metadata.get("provider_status")
    )
    if status in {"", "success", "successful", "completed", "complete", "confirmed", "posted"}:
        return None

    transaction_type = _first_text(metadata, "transaction_type").lower()
    description = str(item.description or "").lower()
    noun = "transfer" if transaction_type == "transfer" or "transfer" in description else "transaction"
    if status in {"failed", "failure", "declined", "rejected"}:
        return f"Failed {noun}"
    if status in {"reversed", "refunded"}:
        return f"Reversed {noun}"
    if status in {"pending", "processing", "queued", "in progress"}:
        return f"Processing {noun}"
    return None


def _first_text(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _normalize_status(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def _mask_account(account_number: str) -> str:
    digits = "".join(ch for ch in str(account_number) if ch.isdigit())
    return f"···{digits[-4:]}" if digits else ""


def _clean_visible_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for token in ("**", "*"):
        text = text.replace(token, "")
    return "\n".join(line.strip().strip("_").strip() for line in text.splitlines()).strip()
