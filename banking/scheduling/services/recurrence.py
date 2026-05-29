"""Deterministic recurrence utilities for scheduled transactions."""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

SCHEDULE_TIMEZONE = "Africa/Lagos"
DEFAULT_SCHEDULE_TIME_TEXT = "09:00"
DEFAULT_SCHEDULE_TIME_LOCAL = time(hour=9, minute=0)
_TIME_PATTERN = "%H:%M"
_LAGOS_ZONE = ZoneInfo(SCHEDULE_TIMEZONE)


def now_lagos(now_utc: datetime | None = None) -> datetime:
    """Return the current time in Africa/Lagos."""
    if now_utc is None:
        return datetime.now(_LAGOS_ZONE)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)
    return now_utc.astimezone(_LAGOS_ZONE)


def today_lagos(now_utc: datetime | None = None) -> date:
    """Return the current Africa/Lagos date."""
    return now_lagos(now_utc).date()


def format_lagos_schedule_datetime(next_run_at_utc: datetime) -> str:
    """Format a UTC schedule timestamp for user-facing Lagos-time copy."""
    if next_run_at_utc.tzinfo is None:
        next_run_at_utc = next_run_at_utc.replace(tzinfo=UTC)
    local = next_run_at_utc.astimezone(_LAGOS_ZONE)
    return local.strftime("%B %d, %Y at %I:%M %p").replace(" 0", " ") + " WAT"


def normalize_time_local(value: str | None) -> str | None:
    """Normalize local time into HH:MM format."""
    if not value:
        return None
    text = value.strip().lower()
    if not text:
        return None

    for fmt in ("%H:%M", "%H.%M", "%I:%M%p", "%I%p"):
        try:
            parsed = datetime.strptime(text.replace(" ", ""), fmt)
            return parsed.strftime(_TIME_PATTERN)
        except ValueError:
            continue
    return None


def _parse_local_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_local_time(value: str | None) -> time | None:
    normalized = normalize_time_local(value)
    if not normalized:
        return None
    try:
        return datetime.strptime(normalized, _TIME_PATTERN).time()
    except ValueError:
        return None


def _to_utc_naive(local_date: date, local_time: time, timezone: str = SCHEDULE_TIMEZONE) -> datetime:
    tz = ZoneInfo(timezone)
    dt_local = datetime.combine(local_date, local_time, tzinfo=tz)
    return dt_local.astimezone(UTC).replace(tzinfo=None)


def compute_initial_next_run_utc(
    *,
    recurrence_type: str,
    start_date: str | None,
    local_time: str | None,
    day_of_week: int | None = None,
    day_of_month: int | None = None,
    timezone: str = SCHEDULE_TIMEZONE,
    now_utc: datetime | None = None,
) -> datetime | None:
    """Compute first run timestamp from schedule metadata."""
    now_anchor = now_utc or datetime.now(UTC)
    if now_anchor.tzinfo is None:
        now_anchor = now_anchor.replace(tzinfo=UTC)
    now_utc = now_anchor.astimezone(UTC).replace(tzinfo=None)
    base_time = _parse_local_time(local_time) or DEFAULT_SCHEDULE_TIME_LOCAL
    local_start = _parse_local_date(start_date) or today_lagos(now_utc)
    recurrence = (recurrence_type or "").strip().lower()

    if recurrence == "one_time":
        candidate = _to_utc_naive(local_start, base_time, timezone)
        return candidate if candidate >= now_utc else None

    if recurrence == "daily":
        candidate = _to_utc_naive(local_start, base_time, timezone)
        while candidate < now_utc:
            local_start = local_start + timedelta(days=1)
            candidate = _to_utc_naive(local_start, base_time, timezone)
        return candidate

    if recurrence == "weekly":
        target = int(day_of_week) if day_of_week is not None else local_start.weekday()
        target = max(0, min(6, target))
        current = local_start
        delta = (target - current.weekday()) % 7
        run_date = current + timedelta(days=delta)
        candidate = _to_utc_naive(run_date, base_time, timezone)
        if candidate < now_utc:
            run_date = run_date + timedelta(days=7)
            candidate = _to_utc_naive(run_date, base_time, timezone)
        return candidate

    if recurrence == "monthly":
        dom = int(day_of_month) if day_of_month else local_start.day
        dom = max(1, min(31, dom))
        year = local_start.year
        month = local_start.month
        while True:
            last_day = calendar.monthrange(year, month)[1]
            run_date = date(year=year, month=month, day=min(dom, last_day))
            candidate = _to_utc_naive(run_date, base_time, timezone)
            if candidate >= now_utc:
                return candidate
            month += 1
            if month > 12:
                month = 1
                year += 1

    return None


def compute_next_run_utc(
    *,
    recurrence_type: str,
    due_at_utc: datetime,
    local_time: str | None,
    day_of_month: int | None = None,
    timezone: str = SCHEDULE_TIMEZONE,
) -> datetime | None:
    """Compute next run timestamp for an already-dispatched recurring schedule."""
    recurrence = (recurrence_type or "").strip().lower()
    if recurrence == "one_time":
        return None

    tz = ZoneInfo(timezone)
    base_time = _parse_local_time(local_time) or DEFAULT_SCHEDULE_TIME_LOCAL
    due_local = due_at_utc.replace(tzinfo=UTC).astimezone(tz)

    if recurrence == "daily":
        next_date = due_local.date() + timedelta(days=1)
        return _to_utc_naive(next_date, base_time, timezone)

    if recurrence == "weekly":
        next_date = due_local.date() + timedelta(days=7)
        return _to_utc_naive(next_date, base_time, timezone)

    if recurrence == "monthly":
        dom = int(day_of_month) if day_of_month else due_local.day
        dom = max(1, min(31, dom))
        year = due_local.year
        month = due_local.month + 1
        if month > 12:
            month = 1
            year += 1
        last_day = calendar.monthrange(year, month)[1]
        next_date = date(year=year, month=month, day=min(dom, last_day))
        return _to_utc_naive(next_date, base_time, timezone)

    return None
