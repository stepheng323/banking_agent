"""Timezone helpers for query date semantics."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

LAGOS_TIMEZONE = "Africa/Lagos"
_LAGOS_ZONE = ZoneInfo(LAGOS_TIMEZONE)


def lagos_today(now_utc: datetime | None = None) -> date:
    """Return current date in Africa/Lagos."""
    anchor = now_utc if now_utc is not None else datetime.now(UTC)
    anchor = anchor.replace(tzinfo=UTC) if anchor.tzinfo is None else anchor.astimezone(UTC)
    return anchor.astimezone(_LAGOS_ZONE).date()


def to_lagos_date(value: datetime) -> date:
    """Convert datetime to Africa/Lagos local date.

    Naive datetimes are treated as UTC.
    """
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.astimezone(_LAGOS_ZONE).date()
