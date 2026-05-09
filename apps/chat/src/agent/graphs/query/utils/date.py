"""Date utilities - minimal helpers for date operations.

The LLM resolves natural language dates. This module only provides:
- Comparison period calculation for "vs last month" queries
- Date validation
"""

from datetime import date, timedelta

from apps.chat.src.agent.graphs.query.models import TimeRange


def get_comparison_period(
    current_range: TimeRange,
    comparison_type: str = "previous",
) -> TimeRange:
    """
    Get the comparison period for time-based comparisons.

    Args:
        current_range: The current time range
        comparison_type: "previous" for last period, "year_ago" for same period last year

    Returns:
        TimeRange for the comparison period
    """
    duration = (current_range.end - current_range.start).days + 1

    if comparison_type == "previous":
        end = current_range.start - timedelta(days=1)
        start = end - timedelta(days=duration - 1)
    else:  # year_ago
        start = current_range.start.replace(year=current_range.start.year - 1)
        end = current_range.end.replace(year=current_range.end.year - 1)

    return TimeRange(start=start, end=end, granularity=current_range.granularity)


def get_default_time_range(days: int = 30) -> TimeRange:
    """Get default time range (last N days)."""
    today = date.today()
    return TimeRange(start=today - timedelta(days=days), end=today, granularity="day")


def validate_date_range(time_range: TimeRange) -> bool:
    """Validate that the date range is sensible."""
    if time_range.start > time_range.end:
        return False
    if time_range.start > date.today():
        return False
    # Don't allow ranges older than 2 years
    if (date.today() - time_range.start).days > 730:
        return False
    return True
