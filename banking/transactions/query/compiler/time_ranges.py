"""Time range normalization for query compiler output."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from banking.transactions.query.capabilities import QUERY_LIMITS
from banking.transactions.query.compiler.lexical_recovery import (
    month_token,
    resolve_month_period_with_year_hint,
)
from banking.transactions.query.models.domain import (
    ComparisonDirective,
    QueryFactField,
    QueryIntent,
    TimeRange,
)
from banking.transactions.query.models.extraction import (
    QueryExtractionResult,
    TimeReference,
)


def default_rolling_time_range(today: date) -> TimeRange:
    """Return the inclusive default recent window."""
    days_back = max(int(QUERY_LIMITS["default_lookback_days"]) - 1, 0)
    return TimeRange(start=today - timedelta(days=days_back), end=today, granularity="day")


def resolve_period_to_range(period: str, *, today: date, current_range: TimeRange | None = None) -> TimeRange | None:
    def _duration_days(range_value: TimeRange | None) -> int:
        if range_value is None:
            return 0
        return max(1, (range_value.end - range_value.start).days + 1)

    token = period.strip().lower().replace("-", "_").replace(" ", "_")
    if token in {"today"}:
        return TimeRange(start=today, end=today, granularity="day")
    if token in {"yesterday"}:
        day = today - timedelta(days=1)
        return TimeRange(start=day, end=day, granularity="day")
    if token in {"this_week", "week", "current_week"}:
        week_start = today - timedelta(days=today.weekday())
        return TimeRange(start=week_start, end=today, granularity="week")
    if token in {"last_week", "previous_week"}:
        this_week_start = today - timedelta(days=today.weekday())
        week_end = this_week_start - timedelta(days=1)
        week_start = week_end - timedelta(days=6)
        if "same_period" in token or current_range is not None:
            duration = _duration_days(current_range)
            if duration > 0:
                aligned_end = min(week_end, week_start + timedelta(days=duration - 1))
                return TimeRange(start=week_start, end=aligned_end, granularity="week")
        return TimeRange(start=week_start, end=week_end, granularity="week")
    if token in {"this_month", "current_month", "month"}:
        month_start = date(today.year, today.month, 1)
        return TimeRange(start=month_start, end=today, granularity="month")
    if token in {"last_month", "previous_month", "same_period_last_month"}:
        year = today.year
        month = today.month - 1
        if month == 0:
            month = 12
            year -= 1
        last_day = __import__("calendar").monthrange(year, month)[1]
        month_start = date(year, month, 1)
        month_end = date(year, month, last_day)
        if "same_period" in token or current_range is not None:
            duration = _duration_days(current_range)
            if duration > 0:
                aligned_end = min(month_end, month_start + timedelta(days=duration - 1))
                return TimeRange(start=month_start, end=aligned_end, granularity="month")
        return TimeRange(start=month_start, end=month_end, granularity="month")
    if token in {"this_year", "current_year", "year"}:
        return TimeRange(start=date(today.year, 1, 1), end=today, granularity="month")
    if token in {"last_year", "previous_year"}:
        year = today.year - 1
        return TimeRange(start=date(year, 1, 1), end=date(year, 12, 31), granularity="month")
    hinted_month_range = resolve_month_period_with_year_hint(period, today=today)
    if hinted_month_range is not None:
        return hinted_month_range
    month_number = month_token(token)
    if month_number is not None:
        year = today.year if month_number <= today.month else today.year - 1
        from calendar import monthrange

        month_start = date(year, month_number, 1)
        month_end = date(year, month_number, monthrange(year, month_number)[1])
        if year == today.year and month_number == today.month:
            month_end = today
        return TimeRange(start=month_start, end=month_end, granularity="month")
    return None


def build_comparison_directive(
    extraction: QueryExtractionResult,
    *,
    intent: QueryIntent,
    current_range: TimeRange,
    today: date,
) -> ComparisonDirective | None:
    if intent != QueryIntent.TIME_COMPARISON:
        return None
    comparison = extraction.comparison
    if comparison is None:
        return ComparisonDirective(mode="previous_equivalent")
    if comparison.mode == "year_ago":
        return ComparisonDirective(mode="year_ago")
    if comparison.mode == "explicit_period" and comparison.period:
        explicit_range = resolve_period_to_range(comparison.period, today=today, current_range=current_range)
        if explicit_range is not None:
            return ComparisonDirective(mode="explicit_range", explicit_range=explicit_range)
        return ComparisonDirective(mode="previous_equivalent")
    return ComparisonDirective(mode="previous_equivalent")


def build_time_range(
    extraction: QueryExtractionResult,
    *,
    today: date,
    intent: QueryIntent,
    answer_fact_field: QueryFactField | None,
    result_reference: Literal["latest", "oldest"] | None,
) -> TimeRange | None:
    if not extraction.time_range:
        return None
    days_back = extraction.time_range.days_back
    period_lower = (extraction.time_range.period or "").strip().lower()
    reference_type = extraction.time_range.reference_type
    if reference_type == TimeReference.EXPLICIT and period_lower:
        explicit_range = resolve_period_to_range(period_lower, today=today)
        if explicit_range is not None:
            return explicit_range
    if reference_type == TimeReference.ALL_TIME or (
        reference_type == TimeReference.UNSPECIFIED
        and intent == QueryIntent.TRANSACTION_DETAIL
        and result_reference == "latest"
        and answer_fact_field
        in {
            "date",
            "counterparty",
            "amount",
            "bank",
            "status",
            "description",
            "reference",
            "account",
            "direction",
            "category",
        }
    ):
        return TimeRange(
            start=today - timedelta(days=QUERY_LIMITS["max_lookback_days"]),
            end=today,
            granularity="day",
        )
    if reference_type == TimeReference.UNSPECIFIED:
        if today.day <= 7:
            return default_rolling_time_range(today)
        return TimeRange(start=date(today.year, today.month, 1), end=today, granularity="month")
    if period_lower == "today":
        days_back = 0
    elif period_lower == "yesterday":
        days_back = 1
    if days_back is None:
        days_back = max(int(QUERY_LIMITS["default_lookback_days"]) - 1, 0)
    elif (
        period_lower == "recent_30_days"
        or reference_type == TimeReference.VAGUE
    ) and days_back == QUERY_LIMITS["default_lookback_days"]:
        days_back = max(days_back - 1, 0)
    range_start = today - timedelta(days=days_back)
    range_end = today
    if period_lower == "today":
        range_start = today
        range_end = today
    elif period_lower == "yesterday":
        yesterday = today - timedelta(days=1)
        range_start = yesterday
        range_end = yesterday
    return TimeRange(start=range_start, end=range_end, granularity="day")
