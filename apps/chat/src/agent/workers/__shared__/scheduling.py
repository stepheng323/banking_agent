"""Shared scheduling helpers for transaction workers."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, cast

from shared.i18n.message_keys import MessageKey
from shared.i18n.renderer import render_message
from banking.scheduling.services.recurrence import (
    SCHEDULE_TIMEZONE,
    normalize_time_local,
    now_lagos,
    today_lagos,
)

SCHEDULE_FIELD_NAMES = {
    "schedule_start_date",
    "schedule_time_local",
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
_WEEKDAY_PATTERN = "|".join(_WEEKDAY_NAME_TO_INDEX)


def parse_schedule_time_local(text: str | None) -> str | None:
    if not text:
        return None
    ampm_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text, re.IGNORECASE)
    if ampm_match:
        hour = int(ampm_match.group(1)) % 12
        minute = int(ampm_match.group(2) or 0)
        if ampm_match.group(3).lower() == "pm":
            hour += 12
        return f"{hour:02d}:{minute:02d}"

    hour_match = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if hour_match:
        return normalize_time_local(f"{hour_match.group(1)}:{hour_match.group(2)}")
    return None


def parse_schedule_date(text: str | None, *, now_local: datetime | None = None) -> str | None:
    if not text:
        return None
    lowered = text.lower()
    local_now = now_local or now_lagos()
    if "tomorrow" in lowered or "tommorow" in lowered:
        return (local_now.date() + timedelta(days=1)).isoformat()
    if "today" in lowered:
        return local_now.date().isoformat()

    next_weekday_match = re.search(
        r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        lowered,
        re.IGNORECASE,
    )
    if next_weekday_match:
        target = _WEEKDAY_NAME_TO_INDEX[next_weekday_match.group(1).lower()]
        delta_days = (target - local_now.date().weekday()) % 7
        if delta_days == 0:
            delta_days = 7
        return (local_now.date() + timedelta(days=delta_days)).isoformat()

    weekday_match = re.search(
        rf"\b(?:on\s+|this\s+)?({_WEEKDAY_PATTERN})\b",
        lowered,
        re.IGNORECASE,
    )
    if weekday_match:
        target = _WEEKDAY_NAME_TO_INDEX[weekday_match.group(1).lower()]
        delta_days = (target - local_now.date().weekday()) % 7
        return (local_now.date() + timedelta(days=delta_days)).isoformat()

    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", lowered)
    if iso_match:
        return iso_match.group(1)

    dmy_match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", lowered)
    if dmy_match:
        day = int(dmy_match.group(1))
        month = int(dmy_match.group(2))
        year = int(dmy_match.group(3))
        try:
            return datetime(year=year, month=month, day=day).date().isoformat()
        except ValueError:
            return None
    return None


def parse_schedule_recurrence_patch(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    lowered = text.lower()
    patch: dict[str, Any] = {}

    if re.search(r"\b(?:every\s+month|monthly)\b", lowered):
        patch["schedule_mode"] = "recurring"
        patch["recurrence_type"] = "monthly"
    elif re.search(r"\b(?:every\s+week|weekly)\b", lowered):
        patch["schedule_mode"] = "recurring"
        patch["recurrence_type"] = "weekly"
    elif re.search(r"\b(?:every\s+day|daily)\b", lowered):
        patch["schedule_mode"] = "recurring"
        patch["recurrence_type"] = "daily"

    every_weekday_match = re.search(rf"\bevery\s+({_WEEKDAY_PATTERN})\b", lowered, re.IGNORECASE)
    if every_weekday_match:
        patch["schedule_mode"] = "recurring"
        patch["recurrence_type"] = "weekly"
        patch["schedule_day_of_week"] = _WEEKDAY_NAME_TO_INDEX[every_weekday_match.group(1).lower()]
    elif patch.get("recurrence_type") == "weekly":
        weekday_match = re.search(rf"\b(?:on\s+|this\s+|next\s+)?({_WEEKDAY_PATTERN})\b", lowered, re.IGNORECASE)
        if weekday_match:
            patch["schedule_day_of_week"] = _WEEKDAY_NAME_TO_INDEX[weekday_match.group(1).lower()]

    if patch:
        patch["schedule_timezone"] = SCHEDULE_TIMEZONE
    return patch


def parse_schedule_slot_patch(user_message: str, required_fields: list[str]) -> tuple[dict[str, Any], list[str]]:
    patch: dict[str, Any] = {}
    remaining: list[str] = []
    patch.update(parse_schedule_recurrence_patch(user_message))

    should_parse_date = bool(required_fields) or bool(patch)
    if should_parse_date:
        schedule_date = parse_schedule_date(user_message)
        if schedule_date:
            patch["schedule_start_date"] = schedule_date
    effective_recurrence_type = str(patch.get("recurrence_type") or "one_time")
    if "schedule_start_date" in required_fields and "schedule_start_date" not in patch:
        if effective_recurrence_type == "one_time":
            remaining.append("schedule_start_date")

    should_parse_time = bool(required_fields) or bool(patch)
    if should_parse_time:
        schedule_time = parse_schedule_time_local(user_message)
        if schedule_time:
            patch["schedule_time_local"] = schedule_time
    if "schedule_time_local" in required_fields and "schedule_time_local" not in patch:
        remaining.append("schedule_time_local")
    if patch:
        patch["confirmation"] = {"confirmed": False}
    return patch, remaining


def schedule_required_prompt(missing_fields: list[str], locale: str = "en") -> str:
    fields = set(missing_fields)
    if fields == {"schedule_time_local"}:
        return render_message("schedule.prompt.time", locale)
    if fields == {"schedule_start_date"}:
        return render_message("schedule.prompt.date", locale)
    return render_message("schedule.prompt.future_date_time", locale)


def schedule_recurrence_label(recurrence_type: Any, locale: str = "en") -> str:
    normalized = str(recurrence_type or "one_time").strip().lower()
    recurrence_key = f"schedule.recurrence.{normalized}"
    return render_message(
        cast(MessageKey, recurrence_key),
        locale,
        fallback_en=normalized.replace("_", " ").title(),
    )


def missing_schedule_fields(data: Any) -> list[str]:
    recurrence_type = str(getattr(data, "recurrence_type", None) or "one_time")
    missing: list[str] = []
    if recurrence_type == "one_time" and not getattr(data, "schedule_start_date", None):
        missing.append("schedule_start_date")
    if not getattr(data, "schedule_time_local", None):
        missing.append("schedule_time_local")
    return missing


def format_schedule_confirmation_line(data: Any, locale: str = "en") -> str | None:
    schedule_time_local = getattr(data, "schedule_time_local", None)
    if not schedule_time_local:
        return None
    date_text = ""
    schedule_start_date = getattr(data, "schedule_start_date", None)
    if schedule_start_date:
        try:
            schedule_date = datetime.strptime(str(schedule_start_date), "%Y-%m-%d").date()
        except ValueError:
            schedule_date = None
        if schedule_date is not None:
            today = today_lagos()
            if schedule_date == today:
                date_text = render_message("schedule.confirmation.today", locale)
            elif schedule_date == today + timedelta(days=1):
                date_text = render_message("schedule.confirmation.tomorrow", locale)
            else:
                date_text = schedule_date.strftime("%Y-%m-%d")
    recurrence_type = getattr(data, "recurrence_type", None)
    if not date_text and recurrence_type and recurrence_type != "one_time":
        date_text = schedule_recurrence_label(recurrence_type, locale)
    if not date_text:
        return None

    try:
        time_text = datetime.strptime(str(schedule_time_local), "%H:%M").strftime("%I:%M %p").lstrip("0")
    except ValueError:
        time_text = str(schedule_time_local)
    return render_message("schedule.confirmation.line", locale, {"date": date_text, "time": time_text})


def base_schedule_fields(data: Any) -> dict[str, Any]:
    recurrence_type = getattr(data, "recurrence_type", None) or "one_time"
    return {
        "schedule_mode": getattr(data, "schedule_mode", None)
        or ("recurring" if recurrence_type != "one_time" else "one_time"),
        "recurrence_type": recurrence_type,
        "schedule_timezone": getattr(data, "schedule_timezone", None) or SCHEDULE_TIMEZONE,
        "schedule_start_date": getattr(data, "schedule_start_date", None),
        "schedule_time_local": getattr(data, "schedule_time_local", None),
        "schedule_day_of_week": getattr(data, "schedule_day_of_week", None),
        "schedule_day_of_month": getattr(data, "schedule_day_of_month", None),
        "schedule_end_date": getattr(data, "schedule_end_date", None),
    }
