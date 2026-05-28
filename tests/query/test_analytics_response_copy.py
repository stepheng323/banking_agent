from datetime import date
from typing import Any

import pytest

from apps.chat.src.agent.workers.query.handlers.analytics import handle_analytics
from apps.chat.src.agent.workers.query.models.domain import (
    Aggregation,
    Filters,
    QueryExecutionContract,
    QueryIntent,
    QueryIR,
    TimeRange,
)
from apps.chat.src.agent.workers.query.presentation.formatter import QueryFormatter
from apps.chat.src.agent.workers.query.presentation.scope import build_transaction_heading
from apps.chat.src.agent.workers.query.services.answers.strategy import select_answer_strategy


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
        "apps.chat.src.agent.workers.query.handlers.analytics.fetch_and_filter",
        _fake_fetch_and_filter,
    )
    monkeypatch.setattr("apps.chat.src.agent.workers.query.handlers.analytics.lagos_today", lambda: date(2026, 3, 6))

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
async def test_analytics_sum_response_names_retained_account_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch_and_filter(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        return [
            {
                "id": "tx_1",
                "amount": 50000,
                "narration": "Transfer to Cowrywise",
                "date": "2026-05-05",
                "type": "debit",
                "bank_name": "First Bank",
            },
            {
                "id": "tx_2",
                "amount": 46200,
                "narration": "Transfer to Dad",
                "date": "2026-05-06",
                "type": "debit",
                "bank_name": "First Bank",
            },
        ]

    monkeypatch.setattr(
        "apps.chat.src.agent.workers.query.handlers.analytics.fetch_and_filter",
        _fake_fetch_and_filter,
    )
    monkeypatch.setattr("apps.chat.src.agent.workers.query.handlers.analytics.lagos_today", lambda: date(2026, 5, 11))

    result = await handle_analytics(
        _Provider(),  # type: ignore[arg-type]
        QueryExecutionContract.from_query_ir(
            QueryIR(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                aggregation=Aggregation(type="sum"),
                filters=Filters(transaction_type="debit", account_filter="First Bank"),
                time_range=TimeRange(start=date(2026, 5, 1), end=date(2026, 5, 11)),
            )
        ),
        account_id="acc_1",
        account_ids=["acc_1", "acc_2"],
        language="en",
    )

    assert result.summary_text == "You spent *₦96,200* with First Bank from May 01 to May 11, across 2 transactions."


@pytest.mark.asyncio
async def test_analytics_sum_response_names_recipient_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch_and_filter(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        return [
            {
                "id": "tx_1",
                "amount": 5000,
                "narration": "Transfer to Tolu Adebayo",
                "date": "2026-05-19",
                "type": "debit",
                "counterparty": "Tolu Adebayo",
            },
            {
                "id": "tx_2",
                "amount": 10000,
                "narration": "Transfer to Tolu Adebayo",
                "date": "2026-05-20",
                "type": "debit",
                "counterparty": "Tolu Adebayo",
            },
        ]

    monkeypatch.setattr(
        "apps.chat.src.agent.workers.query.handlers.analytics.fetch_and_filter",
        _fake_fetch_and_filter,
    )
    monkeypatch.setattr("apps.chat.src.agent.workers.query.handlers.analytics.lagos_today", lambda: date(2026, 5, 20))

    result = await handle_analytics(
        _Provider(),  # type: ignore[arg-type]
        QueryExecutionContract.from_query_ir(
            QueryIR(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                aggregation=Aggregation(type="sum"),
                filters=Filters(transaction_type="debit", counterparty=["tolu"]),
                time_range=TimeRange(start=date(2026, 5, 1), end=date(2026, 5, 20)),
            )
        ),
        account_id="acc_1",
        account_ids=["acc_1"],
        language="en",
    )

    assert result.summary_text == "You sent *₦15,000* to Tolu from May 01 to May 20, across 2 transactions."


def test_transaction_heading_title_cases_lowercase_recipient_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.chat.src.agent.workers.query.presentation.scope.lagos_today",
        lambda: date(2026, 5, 20),
    )

    heading = build_transaction_heading(
        QueryExecutionContract.from_query_ir(
            QueryIR(
                intent=QueryIntent.TRANSACTION_LIST,
                filters=Filters(transaction_type="debit", counterparty=["tolu"]),
                time_range=TimeRange(start=date(2026, 5, 1), end=date(2026, 5, 20)),
            )
        ),
        locale="en",
    )

    assert heading == "*Payments to Tolu* — This Month"


@pytest.mark.asyncio
async def test_analytics_sum_no_spending_today_is_humanized(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch_and_filter(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        return []

    monkeypatch.setattr(
        "apps.chat.src.agent.workers.query.handlers.analytics.fetch_and_filter",
        _fake_fetch_and_filter,
    )
    monkeypatch.setattr("apps.chat.src.agent.workers.query.handlers.analytics.lagos_today", lambda: date(2026, 3, 6))

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
        "apps.chat.src.agent.workers.query.handlers.analytics.fetch_and_filter",
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


@pytest.mark.asyncio
async def test_analytics_account_breakdown_includes_safe_account_suffix_when_available() -> None:
    provider = _MultiAccountProvider(
        {
            "acc_1": [
                {"id": "tx_1", "amount": 32000, "narration": "Transfer to Mum", "date": "2026-03-05", "type": "debit"}
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
        account_ids=["acc_1"],
        accounts_info=[{"account_id": "acc_1", "bank_name": "First Bank", "account_number": "6000000001"}],
        language="en",
    )

    assert [item.description for item in result.items or []] == ["First Bank (···0001)"]
    assert (result.items or [])[0].metadata["account_suffix"] == "···0001"


@pytest.mark.asyncio
async def test_category_breakdown_marks_provider_category_as_provider_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch_and_filter(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        return [
            {
                "id": "tx_food",
                "amount": 4500,
                "narration": "Lunch",
                "date": "2026-03-05",
                "type": "debit",
                "category": "food",
                "resolved_category": "food",
                "category_source": "provider",
            }
        ]

    monkeypatch.setattr(
        "apps.chat.src.agent.workers.query.handlers.analytics.fetch_and_filter",
        _fake_fetch_and_filter,
    )

    result = await handle_analytics(
        _Provider(),  # type: ignore[arg-type]
        QueryExecutionContract.from_query_ir(
            QueryIR(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                aggregation=Aggregation(type="breakdown", group_by="category"),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 6)),
            )
        ),
        account_id="acc_1",
        account_ids=["acc_1"],
        language="en",
    )

    assert (result.items or [])[0].description == "food"
    assert (result.items or [])[0].metadata["category_confidence"] == "provider"


@pytest.mark.asyncio
async def test_category_breakdown_marks_narration_category_as_inferred(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch_and_filter(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        return [
            {
                "id": "tx_glovo",
                "amount": 4500,
                "narration": "GLOVO Food Delivery",
                "date": "2026-03-05",
                "type": "debit",
                "resolved_category": "food",
                "category_source": "narration_rule",
            }
        ]

    monkeypatch.setattr(
        "apps.chat.src.agent.workers.query.handlers.analytics.fetch_and_filter",
        _fake_fetch_and_filter,
    )

    result = await handle_analytics(
        _Provider(),  # type: ignore[arg-type]
        QueryExecutionContract.from_query_ir(
            QueryIR(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                aggregation=Aggregation(type="breakdown", group_by="category"),
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 6)),
            )
        ),
        account_id="acc_1",
        account_ids=["acc_1"],
        language="en",
    )

    assert (result.items or [])[0].description == "food"
    assert (result.items or [])[0].metadata["category_confidence"] == "inferred"


@pytest.mark.asyncio
async def test_single_largest_debit_renders_detail_not_heading_only(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch_and_filter(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        return [
            {
                "id": "tx_biggest",
                "amount": 250000,
                "narration": "Transfer to Landlord",
                "date": "2026-04-05",
                "type": "debit",
                "bank_name": "Zenith Bank",
            },
            {
                "id": "tx_small",
                "amount": 12000,
                "narration": "Transfer to Mum",
                "date": "2026-04-04",
                "type": "debit",
                "bank_name": "Opay",
            },
        ]

    monkeypatch.setattr(
        "apps.chat.src.agent.workers.query.handlers.analytics.fetch_and_filter",
        _fake_fetch_and_filter,
    )

    result = await handle_analytics(
        _Provider(),  # type: ignore[arg-type]
        QueryExecutionContract.from_query_ir(
            QueryIR(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                aggregation=Aggregation(type="largest", limit=1),
                filters=Filters(transaction_type="debit"),
                time_range=TimeRange(start=date(2026, 4, 1), end=date(2026, 4, 8)),
            )
        ),
        account_id="acc_1",
        account_ids=["acc_1"],
        language="en",
    )

    formatted = QueryFormatter.format(select_answer_strategy(result, locale="en"), locale="en")

    assert "Your biggest expense from Apr 01 to Apr 08" in formatted
    assert "₦250,000" in formatted
    assert "Transfer to Landlord" in formatted
