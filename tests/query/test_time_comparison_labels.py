from datetime import date

from apps.core.src.agent.graphs.query.handlers.time_comparison import _format_period_label
from apps.core.src.agent.graphs.query.models import TimeRange


def test_format_period_label_keeps_full_month_name() -> None:
    label = _format_period_label(
        TimeRange(start=date(2026, 2, 1), end=date(2026, 2, 28), granularity="month")
    )

    assert label == "February 2026"


def test_format_period_label_uses_date_span_for_partial_month() -> None:
    label = _format_period_label(
        TimeRange(start=date(2026, 2, 1), end=date(2026, 2, 7), granularity="month")
    )

    assert label == "Feb 01 - Feb 07"


def test_format_period_label_uses_date_span_for_partial_week() -> None:
    label = _format_period_label(
        TimeRange(start=date(2026, 2, 23), end=date(2026, 2, 27), granularity="week")
    )

    assert label == "Feb 23 - Feb 27"
