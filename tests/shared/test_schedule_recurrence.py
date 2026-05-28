from datetime import UTC, datetime

from apps.chat.src.agent.workers.__shared__.scheduling import parse_schedule_date, parse_schedule_slot_patch
from shared.services.scheduling.recurrence import (
    compute_initial_next_run_utc,
    compute_next_run_utc,
    format_lagos_schedule_datetime,
    now_lagos,
)


def test_compute_initial_weekly_defaults_to_future_due_date() -> None:
    now = datetime(2026, 3, 5, 10, 0, tzinfo=UTC)
    next_run = compute_initial_next_run_utc(
        recurrence_type="weekly",
        start_date="2026-03-05",
        local_time="09:00",
        day_of_week=4,
        timezone="Africa/Lagos",
        now_utc=now,
    )
    assert next_run is not None
    assert next_run > now.replace(tzinfo=None)


def test_compute_initial_one_time_returns_none_for_past_date() -> None:
    now = datetime(2026, 3, 5, 10, 0, tzinfo=UTC)
    next_run = compute_initial_next_run_utc(
        recurrence_type="one_time",
        start_date="2026-03-04",
        local_time="09:00",
        timezone="Africa/Lagos",
        now_utc=now,
    )
    assert next_run is None


def test_compute_next_run_monthly_rolls_to_next_month() -> None:
    due = datetime(2026, 1, 31, 8, 0)
    next_run = compute_next_run_utc(
        recurrence_type="monthly",
        due_at_utc=due,
        local_time="09:00",
        day_of_month=31,
        timezone="Africa/Lagos",
    )
    assert next_run is not None
    assert next_run > due


def test_schedule_date_parsing_uses_lagos_day_boundary() -> None:
    lagos_now = now_lagos(datetime(2026, 5, 21, 23, 30, tzinfo=UTC))

    assert lagos_now.date().isoformat() == "2026-05-22"
    assert parse_schedule_date("today", now_local=lagos_now) == "2026-05-22"
    assert parse_schedule_date("tomorrow", now_local=lagos_now) == "2026-05-23"


def test_schedule_date_parses_bare_weekdays() -> None:
    lagos_now = now_lagos(datetime(2026, 5, 22, 10, 0, tzinfo=UTC))

    assert parse_schedule_date("sunday", now_local=lagos_now) == "2026-05-24"
    assert parse_schedule_date("on sunday", now_local=lagos_now) == "2026-05-24"
    assert parse_schedule_date("this sunday", now_local=lagos_now) == "2026-05-24"


def test_schedule_date_next_weekday_skips_current_day() -> None:
    lagos_now = now_lagos(datetime(2026, 5, 24, 10, 0, tzinfo=UTC))

    assert parse_schedule_date("sunday", now_local=lagos_now) == "2026-05-24"
    assert parse_schedule_date("next sunday", now_local=lagos_now) == "2026-05-31"


def test_schedule_slot_patch_parses_weekday_and_time_together() -> None:
    patch, remaining = parse_schedule_slot_patch(
        "sunday 3pm",
        ["schedule_start_date", "schedule_time_local"],
    )

    assert patch["schedule_start_date"]
    assert patch["schedule_time_local"] == "15:00"
    assert remaining == []


def test_schedule_slot_patch_parses_monthly_recurrence() -> None:
    patch, remaining = parse_schedule_slot_patch(
        "every month",
        ["schedule_start_date", "schedule_time_local"],
    )

    assert patch["schedule_mode"] == "recurring"
    assert patch["recurrence_type"] == "monthly"
    assert patch["schedule_timezone"] == "Africa/Lagos"
    assert remaining == ["schedule_time_local"]


def test_initial_schedule_without_start_date_defaults_to_lagos_date() -> None:
    next_run = compute_initial_next_run_utc(
        recurrence_type="daily",
        start_date=None,
        local_time="01:00",
        timezone="Africa/Lagos",
        now_utc=datetime(2026, 5, 21, 23, 30, tzinfo=UTC),
    )

    assert next_run == datetime(2026, 5, 22, 0, 0)


def test_lagos_schedule_datetime_copy_does_not_show_utc() -> None:
    assert (
        format_lagos_schedule_datetime(datetime(2026, 5, 22, 7, 0))
        == "May 22, 2026 at 8:00 AM WAT"
    )
