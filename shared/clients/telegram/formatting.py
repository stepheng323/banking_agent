"""Telegram-safe text formatting helpers."""

import html
import re


def format_telegram_html(text: str) -> str:
    """Convert lightweight markdown-like syntax to Telegram-safe HTML."""
    escaped = html.escape(text or "")
    escaped = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<b>\1</b>", escaped)

    def _italic_repl(match: re.Match[str]) -> str:
        prefix = match.group(1) or ""
        content = match.group(2) or ""
        return f"{prefix}<i>{content}</i>"

    escaped = re.sub(r"(^|[\s(])_(?!_)([^_\n]+?)_(?=[\s).,!?:;]|$)", _italic_repl, escaped)
    return escaped


def telegram_html_to_plain_text(text: str) -> str:
    """Convert Telegram HTML back to plain text for Mini App UI copy."""
    without_tags = re.sub(
        r"</?(?:b|strong|i|em|code|u|s|strike|del|tg-spoiler|blockquote)(?:\s[^>]*)?>",
        "",
        text or "",
    )
    return html.unescape(without_tags)
