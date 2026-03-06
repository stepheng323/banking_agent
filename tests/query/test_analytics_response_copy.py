from datetime import date
from typing import Any

import pytest

from apps.core.src.agent.graphs.query.handlers.analytics import handle_analytics
from apps.core.src.agent.graphs.query.models import (
    Aggregation,
    Filters,
    NormalizedQuery,
    QueryExecutionContract,
    QueryIntent,
    TimeRange,
)


class _Provider:
    async def get_transactions(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        return []


@pytest.mark.asyncio
async def test_analytics_sum_response_is_compact_and_human(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch_and_filter(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        return [
            {"id": "tx_1", "amount": 32000, "narration": "Transfer to Mum", "date": "2026-03-05", "type": "debit"},
            {"id": "tx_2", "amount": 10000, "narration": "Transfer to Tolu", "date": "2026-03-05", "type": "debit"},
        ]

    monkeypatch.setattr(
        "apps.core.src.agent.graphs.query.handlers.analytics.fetch_and_filter",
        _fake_fetch_and_filter,
    )
    monkeypatch.setattr("apps.core.src.agent.graphs.query.handlers.analytics.lagos_today", lambda: date(2026, 3, 6))

    result = await handle_analytics(
        _Provider(),  # type: ignore[arg-type]
        QueryExecutionContract.from_normalized_query(
            NormalizedQuery(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                aggregation=Aggregation(type="sum"),
                filters=Filters(transaction_type="debit"),
                time_range=TimeRange(start=date(2026, 3, 5), end=date(2026, 3, 5)),
            )
        ),
        account_id="acc_1",
        account_ids=["acc_1"],
        language="en",
    )

    assert result.summary_text == "You spent *₦42,000* yesterday, across 2 transactions."


@pytest.mark.asyncio
async def test_analytics_sum_no_spending_today_is_humanized(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch_and_filter(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        return []

    monkeypatch.setattr(
        "apps.core.src.agent.graphs.query.handlers.analytics.fetch_and_filter",
        _fake_fetch_and_filter,
    )
    monkeypatch.setattr("apps.core.src.agent.graphs.query.handlers.analytics.lagos_today", lambda: date(2026, 3, 6))

    result = await handle_analytics(
        _Provider(),  # type: ignore[arg-type]
        QueryExecutionContract.from_normalized_query(
            NormalizedQuery(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                aggregation=Aggregation(type="sum"),
                filters=Filters(transaction_type="debit"),
                time_range=TimeRange(start=date(2026, 3, 6), end=date(2026, 3, 6)),
            )
        ),
        account_id="acc_1",
        account_ids=["acc_1"],
        language="en",
    )

    assert result.summary_text == "You didn't spend anything today."
