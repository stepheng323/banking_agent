from datetime import date
from typing import Any

import pytest

from apps.core.src.agent.graphs.query.handlers.analytics import handle_analytics
from apps.core.src.agent.graphs.query.models import (
    Aggregation,
    Filters,
    QueryExecutionContract,
    QueryIntent,
    QueryIR,
    TimeRange,
)


class _Provider:
    async def get_transactions(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        return []


class _MultiAccountProvider:
    def __init__(self, transactions_by_account: dict[str, list[dict[str, Any]]]) -> None:
        self.transactions_by_account = transactions_by_account

    async def get_transactions(
        self,
        account_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        del args, kwargs
        return list(self.transactions_by_account.get(account_id, []))


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
        QueryExecutionContract.from_query_ir(
            QueryIR(
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
        QueryExecutionContract.from_query_ir(
            QueryIR(
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


@pytest.mark.asyncio
async def test_analytics_transaction_type_breakdown_uses_human_label(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch_and_filter(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        return [
            {"id": "tx_1", "amount": 32000, "narration": "Transfer to Mum", "date": "2026-03-05", "type": "debit"},
            {"id": "tx_2", "amount": 950000, "narration": "Salary", "date": "2026-03-05", "type": "credit"},
        ]

    monkeypatch.setattr(
        "apps.core.src.agent.graphs.query.handlers.analytics.fetch_and_filter",
        _fake_fetch_and_filter,
    )

    result = await handle_analytics(
        _Provider(),  # type: ignore[arg-type]
        QueryExecutionContract.from_query_ir(
            QueryIR(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                aggregation=Aggregation(type="breakdown", group_by="transaction_type"),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 6)),
            )
        ),
        account_id="acc_1",
        account_ids=["acc_1"],
        language="en",
    )

    assert result.summary_text == "Breakdown by transaction type"
    assert [item.description for item in result.items] == ["credit", "debit"]


@pytest.mark.asyncio
async def test_analytics_account_breakdown_groups_by_source_account_label() -> None:
    provider = _MultiAccountProvider(
        {
            "acc_1": [
                {"id": "tx_1", "amount": 32000, "narration": "Transfer to Mum", "date": "2026-03-05", "type": "debit"}
            ],
            "acc_2": [
                {"id": "tx_2", "amount": 18000, "narration": "Transfer to Tolu", "date": "2026-03-05", "type": "debit"}
            ],
        }
    )

    result = await handle_analytics(
        provider,  # type: ignore[arg-type]
        QueryExecutionContract.from_query_ir(
            QueryIR(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                aggregation=Aggregation(type="breakdown", group_by="account"),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 6)),
            )
        ),
        account_id="acc_1",
        account_ids=["acc_1", "acc_2"],
        accounts_info=[
            {"account_id": "acc_1", "bank_name": "First Bank"},
            {"account_id": "acc_2", "bank_name": "Access Bank"},
        ],
        language="en",
    )

    assert result.summary_text == "Breakdown by account"
    assert [item.description for item in result.items or []] == ["First Bank", "Access Bank"]
    assert [item.amount for item in result.items or []] == [32000, 18000]
