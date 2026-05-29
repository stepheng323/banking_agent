"""Schedule parsing helpers for planner task payloads."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from apps.chat.src.agent.workers.__shared__.scheduling import parse_schedule_date
from banking.scheduling.services.recurrence import (
    SCHEDULE_TIMEZONE,
    normalize_time_local,
    now_lagos,
)

SCHEDULE_DATE_PATTERN = r"(?:tomorrow|tommorow|today|later|next\s+\w+|on\s+\d{4}-\d{2}-\d{2})"
SCHEDULE_MANAGEMENT_ACTIONS = {
    "list_scheduled_transfers",
    "cancel_scheduled_transfer",
    "list_scheduled_transactions",
    "find_scheduled_transaction",
    "cancel_scheduled_transaction",
    "edit_scheduled_transaction",
}
_WEEKDAY_NAME_TO_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def infer_schedule_action_from_text(user_text: str) -> str | None:
    normalized = user_text.lower()
    if re.search(r"\b(?:every|daily|weekly|monthly)\b", normalized):
        return "recurring_transfer"
    if re.search(rf"\b{SCHEDULE_DATE_PATTERN}\b", normalized):
        return "schedule_transfer"
    return None


def derive_schedule_selector_from_user_text(user_text: str) -> str | None:
    match = re.search(r"\b(?:schedule\s+)?(\d{1,3})\b", user_text.lower())
    if match:
        return match.group(1)
    return None


def derive_transfer_schedule_fields(
    user_text: str,
    *,
    schedule_text: str | None,
    scheduled_text: str | None,
    recurring_flag: bool | None,
) -> dict[str, Any]:
    raw_text = " ".join(filter(None, [user_text, schedule_text, scheduled_text])).strip().lower()
    now_local = now_lagos()

    time_local = _extract_time_local(raw_text)
    is_recurring = bool(recurring_flag) or re.search(r"\b(?:every|daily|weekly|monthly)\b", raw_text) is not None
    recurrence_type = "one_time"
    schedule_mode = "one_time"
    schedule_start_date: str | None = None
    schedule_day_of_week: int | None = None
    schedule_day_of_month: int | None = None

    if is_recurring:
        schedule_mode = "recurring"
        recurrence_type = "daily"

        if re.search(r"\b(?:every\s+month|monthly)\b", raw_text):
            recurrence_type = "monthly"

        weekday_match = re.search(
            r"\bevery\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            raw_text,
        )
        if weekday_match:
            recurrence_type = "weekly"
            schedule_day_of_week = _WEEKDAY_NAME_TO_INDEX.get(weekday_match.group(1))

        monthly_match = re.search(r"\bevery\s+month(?:\s+on)?\s+(\d{1,2})(?:st|nd|rd|th)?\b", raw_text)
        if monthly_match:
            recurrence_type = "monthly"
            schedule_day_of_month = max(1, min(31, int(monthly_match.group(1))))

        if "every day" in raw_text or "daily" in raw_text:
            recurrence_type = "daily"

        schedule_start_date = _extract_date(raw_text, now_local=now_local) or now_local.date().isoformat()
    else:
        recurrence_type = "one_time"
        schedule_mode = "one_time"
        schedule_start_date = _extract_date(raw_text, now_local=now_local)

    patch: dict[str, Any] = {
        "schedule_mode": schedule_mode,
        "recurrence_type": recurrence_type,
        "schedule_timezone": SCHEDULE_TIMEZONE,
    }
    if time_local:
        patch["schedule_time_local"] = time_local
    if schedule_start_date:
        patch["schedule_start_date"] = schedule_start_date
    if schedule_day_of_week is not None:
        patch["schedule_day_of_week"] = schedule_day_of_week
    if schedule_day_of_month is not None:
        patch["schedule_day_of_month"] = schedule_day_of_month
    return patch


def _extract_time_local(text: str) -> str | None:
    ampm_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text)
    if ampm_match:
        hour = int(ampm_match.group(1)) % 12
        minute = int(ampm_match.group(2) or 0)
        if ampm_match.group(3) == "pm":
            hour += 12
        return f"{hour:02d}:{minute:02d}"

    hour_match = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if hour_match:
        normalized = normalize_time_local(f"{hour_match.group(1)}:{hour_match.group(2)}")
        return normalized
    return None


def _extract_date(text: str, *, now_local: datetime) -> str | None:
    return parse_schedule_date(text, now_local=now_local)
