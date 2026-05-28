"""Internal helpers for transaction copy formatters."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from shared.formatters.currency import format_amount_number, format_naira, format_naira_compact
from shared.i18n.renderer import render_message


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _field(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


def _mapping(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return dict(payload)
    if hasattr(payload, "model_dump"):
        dumped = payload.model_dump(exclude_none=True)
        if isinstance(dumped, dict):
            return dumped
    return {}


def _metadata(payload: Any) -> dict[str, Any]:
    metadata = _field(payload, "metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def _format_amount_plain(amount: Any, *, detail: bool = False) -> str:
    if detail:
        return format_naira(amount, decimal_places=2, absolute=True)
    return format_naira_compact(amount, absolute=True)


def _format_support_amount_value(amount: Any) -> str:
    return format_amount_number(amount)


def _format_short_date(value: Any) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%b %d").replace(" 0", " ")
    raw_date = _string(value)
    try:
        parsed = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return raw_date[:10] if raw_date else ""
    return parsed.strftime("%b %d").replace(" 0", " ")


def _format_long_date(value: Any, *, locale: str) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%B %d, %Y")
    raw_date = _string(value)
    try:
        parsed = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return render_message("query.format.unknown", locale)
    return parsed.strftime("%B %d, %Y")


def _parse_support_time(value: Any) -> str:
    raw = _string(value)
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    return parsed.strftime("%b %d at %I:%M %p")


def _display_reference(payload: Any, metadata: dict[str, Any]) -> str | None:
    for key in ("transaction_id", "reference", "ref"):
        value = _string(metadata.get(key))
        if value:
            return value

    item_id = _string(_field(payload, "id"))
    if not item_id or re.fullmatch(r"\d+", item_id):
        return None
    return item_id


def _extract_phone_recipient(description: str) -> str | None:
    phone_match = re.search(r"(\d{10,11})", description or "")
    return phone_match.group(1) if phone_match else None


def _normalize_status_for_copy(status: Any) -> str:
    raw = getattr(status, "value", status)
    normalized = str(raw or "").strip().lower().replace("-", " ").replace("_", " ")
    if "." in normalized:
        normalized = normalized.rsplit(".", 1)[-1]
    return " ".join(normalized.split())
