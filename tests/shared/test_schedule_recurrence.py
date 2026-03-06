from datetime import UTC, datetime

from shared.services.scheduling.recurrence import compute_initial_next_run_utc, compute_next_run_utc


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
