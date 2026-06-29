from datetime import date

import pytest

from banking.transactions.query.handlers.time_comparison import (
    _format_period_label,
    handle_time_comparison,
)
from banking.transactions.query.models.domain import (
    QueryExecutionContract,
    QueryIntent,
    QueryIR,
    TimeRange,
)


class _Provider:
    pass


def test_format_period_label_keeps_full_month_name() -> None:
    label = _format_period_label(TimeRange(start=date(2026, 2, 1), end=date(2026, 2, 28), granularity="month"))

    assert label == "February 2026"


def test_format_period_label_uses_date_span_for_partial_month() -> None:
    label = _format_period_label(TimeRange(start=date(2026, 2, 1), end=date(2026, 2, 7), granularity="month"))

    assert label == "Feb 01 - Feb 07"


def test_format_period_label_uses_date_span_for_partial_week() -> None:
    label = _format_period_label(TimeRange(start=date(2026, 2, 23), end=date(2026, 2, 27), granularity="week"))

    assert label == "Feb 23 - Feb 27"


@pytest.mark.asyncio
async def test_time_comparison_uses_naira_amounts_without_kobo_division(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_query = QueryIR(
        intent=QueryIntent.TIME_COMPARISON,
        time_range=TimeRange(start=date(2026, 3, 8), end=date(2026, 3, 14), granularity="week"),
    )
    calls = 0

    async def _fake_fetch_and_filter(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        del args, kwargs
        calls += 1
        if calls == 1:
            return [
                {"id": "current", "amount": 5000, "type": "debit", "status": "successful"},
                {"id": "failed", "amount": 50000, "type": "debit", "display_status": "failed"},
            ]
        return [{"id": "previous", "amount": 3000, "type": "debit", "status": "successful"}]

    monkeypatch.setattr(
        "banking.transactions.query.handlers.time_comparison.fetch_and_filter",
        _fake_fetch_and_filter,
    )

    result = await handle_time_comparison(
        _Provider(),  # type: ignore[arg-type]
        QueryExecutionContract.from_query_ir(current_query),
        account_id="acc_1",
        account_ids=["acc_1"],
        language="en",
    )

    spending_item = next(item for item in result.items or [] if item.id == "spending")
    assert spending_item.amount == 5000
    assert spending_item.metadata == {
        "current": 5000.0,
        "comparison": 3000.0,
        "change": 2000.0,
        "pct_change": pytest.approx(66.66666666666666),
    }
