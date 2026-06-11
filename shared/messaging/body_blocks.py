"""Structured message body blocks for mobile-friendly rendering."""

from __future__ import annotations

from typing import Any, TypeAlias

from shared.clients.telegram.formatting import format_telegram_html

MessageBlock: TypeAlias = dict[str, Any]
MessageDocument: TypeAlias = list[MessageBlock]

_STATUS_ICONS = {
    "success": "✓",
    "processing": "…",
    "failed": "✗",
}


def normalize_body_blocks(value: Any) -> MessageDocument | None:
    """Return a shallow-normalized message document or None."""
    if not isinstance(value, list):
        return None
    blocks = [dict(item) for item in value if isinstance(item, dict)]
    return blocks or None


def render_body_blocks_text(blocks: MessageDocument | None) -> str:
    """Render message blocks to compact plain/WhatsApp-style text."""
    if not blocks:
        return ""
    sections: list[str] = []
    for block in blocks:
        rendered = _render_block(block)
        if rendered:
            sections.append(rendered)
    return "\n\n".join(sections).strip()


def render_body_blocks_telegram_html(blocks: MessageDocument | None) -> str:
    """Render message blocks to Telegram-safe HTML."""
    return format_telegram_html(render_body_blocks_text(blocks))


def _render_block(block: MessageBlock) -> str:
    block_type = str(block.get("type") or "").strip().lower()
    if block_type == "spacer":
        return ""
    if block_type == "heading":
        return _clean_text(block.get("text"))
    if block_type == "text":
        return _clean_text(block.get("text"))
    if block_type == "key_value":
        label = _clean_text(block.get("label")).rstrip(":")
        value = _clean_text(block.get("value"))
        if label and value:
            return f"{label}: {value}"
        return value or label
    if block_type == "bullet_list":
        return _render_bullet_list(block)
    if block_type == "transaction_item":
        return _render_transaction_item(block)
    return _clean_text(block.get("text"))


def _render_bullet_list(block: MessageBlock) -> str:
    lines: list[str] = []
    title = _clean_text(block.get("title"))
    if title:
        lines.append(title)
    items = block.get("items")
    if isinstance(items, list):
        for item in items:
            text = _clean_text(item)
            if text:
                lines.append(f"• {text}")
    return "\n".join(lines)


def _render_transaction_item(block: MessageBlock) -> str:
    title = _clean_text(block.get("title"))
    subtitle = _clean_text(block.get("subtitle"))
    status = str(block.get("status") or "").strip().lower()
    icon = _STATUS_ICONS.get(status)
    if icon and title and not title.startswith(tuple(_STATUS_ICONS.values())):
        title = f"{icon} {title}"

    lines = [line for line in (title, subtitle) if line]
    details = block.get("details")
    if isinstance(details, list):
        for item in details:
            text = _render_detail(item)
            if text:
                lines.append(text)
    reason = _clean_text(block.get("reason"))
    if reason:
        lines.append(f"Reason: {reason}")
    return "\n".join(lines)


def _render_detail(item: Any) -> str:
    if isinstance(item, dict):
        label = _clean_text(item.get("label")).rstrip(":")
        value = _clean_text(item.get("value"))
        if label and value:
            return f"{label}: {value}"
        return value or label
    return _clean_text(item)


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    return text


__all__ = [
    "MessageBlock",
    "MessageDocument",
    "normalize_body_blocks",
    "render_body_blocks_telegram_html",
    "render_body_blocks_text",
]
