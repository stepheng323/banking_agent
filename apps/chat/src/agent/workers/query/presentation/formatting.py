"""Shared formatting helpers for query presentation plans."""

from __future__ import annotations

from datetime import date, datetime

from shared.formatters.currency import format_naira
from shared.i18n.renderer import render_message


def format_query_amount(amount: float) -> str:
    return format_naira(amount, absolute=True)


def format_query_percentage(amount: float, total_abs: float) -> str:
    if total_abs <= 0:
        return "0%"
    pct = (abs(amount) / total_abs) * 100
    if 0 < pct < 1:
        return "<1%"
    return f"{int(pct)}%"


def parse_summary_parts(summary_text: str | None) -> dict[str, str]:
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


def format_query_date(value: date | str, *, locale: str) -> str:
    if isinstance(value, str):
        raw_date = value
        try:
            value = datetime.strptime(value[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return raw_date[:10] if raw_date else render_message("query.format.unknown", locale)
    return value.strftime("%b %d").replace(" 0", " ")
