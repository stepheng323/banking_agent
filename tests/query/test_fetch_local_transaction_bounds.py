from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from banking.transactions.query.handlers.transactions import handle_transaction_list
from banking.transactions.query.models.domain import (
    Filters,
    QueryIntent,
    QueryRequest,
    TimeRange,
)
from banking.transactions.query.presentation.formatter import QueryFormatter
from banking.transactions.query.services.fetching.fetch import apply_filters
from banking.transactions.services.unified_transactions import UnifiedTransactionService
from shared.config.settings import settings
from tests.query.factories import make_query_request


def _query_ir(**kwargs: object) -> QueryRequest:
    fallback_day = date(2026, 3, 6)
    defaults: dict[str, object] = {
        "intent": QueryIntent.TRANSACTION_LIST,
        "time_range": TimeRange(start=fallback_day, end=fallback_day),
    }
    defaults.update(kwargs)
    return make_query_request(**defaults)


class _Provider:
    async def get_transactions(
        self,
        account_id: str,
        start_date: str,
        end_date: str,
        limit: int = 100,
        user_id: str | None = None,
        mock_account_slot: int | None = None,
    ) -> list[dict[str, Any]]:
        del account_id, start_date, end_date, limit, user_id, mock_account_slot
        return []


class _ProviderWithRows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def get_transactions(
        self,
        account_id: str,
        start_date: str,
        end_date: str,
        limit: int = 100,
        user_id: str | None = None,
        mock_account_slot: int | None = None,
    ) -> list[dict[str, Any]]:
        del account_id, start_date, end_date, limit, user_id, mock_account_slot
        return list(self.rows)


def _query_for_today(today: date) -> QueryRequest:
    return _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today, end=today),
        filters=Filters(transaction_type="debit"),
    )


def _contract(query: QueryRequest) -> QueryRequest:
    return query.model_copy(deep=True)


def test_amount_filter_respects_strict_minimum_bound() -> None:
    rows = [
        {"id": "equal", "amount": 50000, "type": "debit"},
        {"id": "above", "amount": 50001, "type": "debit"},
    ]

    result = apply_filters(
        rows,
        Filters(transaction_type="debit", min_amount=50000, min_amount_inclusive=False),
    )

    assert [row["id"] for row in result] == ["above"]


def test_amount_filter_keeps_inclusive_minimum_bound() -> None:
    rows = [
        {"id": "equal", "amount": 50000, "type": "debit"},
        {"id": "above", "amount": 50001, "type": "debit"},
    ]

    result = apply_filters(
        rows,
        Filters(transaction_type="debit", min_amount=50000, min_amount_inclusive=True),
    )

    assert [row["id"] for row in result] == ["equal", "above"]


def test_status_filter_matches_failed_transactions() -> None:
    rows = [
        {"id": "success", "amount": 50000, "type": "debit", "status": "successful"},
        {"id": "failed", "amount": 50000, "type": "debit", "display_status": "failed"},
        {"id": "pending", "amount": 50000, "type": "debit", "status": "pending"},
    ]

    result = apply_filters(rows, Filters(status="failed"))

    assert [row["id"] for row in result] == ["failed"]


@pytest.fixture(autouse=True)
def _disable_unified_transaction_view(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "enable_unified_transaction_view", False)


@pytest.mark.asyncio
async def test_query_results_are_bank_feed_only_when_provider_returns_no_transactions() -> None:
    query_day = date(2026, 3, 6)

    result = await handle_transaction_list(
        _Provider(),  # type: ignore[arg-type]
        _contract(_query_for_today(query_day)),
        account_id="acc_1",
        account_ids=["acc_1"],
        user_id="user_1",
        language="en",
    )

    assert result.items == []


@pytest.mark.asyncio
async def test_transaction_list_excludes_failed_rows_unless_status_filter_requested() -> None:
    query_day = date(2026, 3, 6)
    rows = [
        {
            "id": "success",
            "amount": 2000,
            "type": "debit",
            "status": "successful",
            "date": query_day.isoformat(),
            "narration": "Transfer to Ada",
        },
        {
            "id": "failed",
            "amount": 3000,
            "type": "debit",
            "display_status": "failed",
            "date": query_day.isoformat(),
            "narration": "Failed transfer to Ada",
        },
    ]

    default_result = await handle_transaction_list(
        _ProviderWithRows(rows),  # type: ignore[arg-type]
        _contract(_query_for_today(query_day)),
        account_id="acc_1",
        account_ids=["acc_1"],
        user_id="user_1",
        language="en",
    )
    failed_result = await handle_transaction_list(
        _ProviderWithRows(rows),  # type: ignore[arg-type]
        _contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=TimeRange(start=query_day, end=query_day),
                filters=Filters(status="failed"),
            )
        ),
        account_id="acc_1",
        account_ids=["acc_1"],
        user_id="user_1",
        language="en",
    )

    assert [item.id for item in default_result.items or []] == ["success"]
    assert [item.id for item in failed_result.items or []] == ["failed"]


@pytest.mark.asyncio
async def test_today_query_with_no_bank_feed_rows_returns_no_results_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query_day = date(2026, 3, 6)
    monkeypatch.setattr(
        "banking.transactions.query.presentation.presentation_planner.lagos_today",
        lambda: query_day,
    )

    query = _query_for_today(query_day)
    result = await handle_transaction_list(
        _Provider(),  # type: ignore[arg-type]
        _contract(query),
        account_id="acc_1",
        account_ids=["acc_1"],
        user_id="user_1",
        language="en",
    )
    result.query_request = _contract(query)

    assert QueryFormatter.format(result, locale="en") == "You had no debit transactions today."


@pytest.mark.asyncio
async def test_unified_view_includes_newer_local_transaction_before_bank_feed_acme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 5, 1), end=date(2026, 5, 17)),
        filters=Filters(status="failed"),
        result_reference="latest",
        result_limit=1,
    )
    provider = _ProviderWithRows(
        [
            {
                "id": "txn_b01",
                "date": "2026-05-13T10:30:00",
                "narration": "Salary from Acme Corp",
                "amount": 950000,
                "type": "credit",
                "category": "income",
                "counterparty": "Acme Corp",
            }
        ]
    )
    local_transfer = SimpleNamespace(
        id="local-1",
        transaction_type="transfer",
        status="failed",
        amount=6000.0,
        currency="NGN",
        recipient_name="Tolu Adebayo",
        recipient_account_number="1234567890",
        recipient_bank_name="Kuda",
        recipient_bank_code="999999",
        source_bank_name="GTBank",
        source_account_number="0123456789",
        transaction_id="local-provider-1",
        idempotency_key="idem-local-1",
        provider_response={},
        provider_status=None,
        provider_error_code=None,
        error_message="Provider timeout",
        failure_category="provider_error",
        narration="Transfer to Tolu Adebayo",
        created_at=datetime(2026, 5, 16, 9, 0, 0),
        updated_at=datetime(2026, 5, 16, 9, 1, 0),
        completed_at=None,
    )

    async def _local_rows(
        self: UnifiedTransactionService,
        user_id: str,
        *,
        start_date: date,
        end_date: date,
        limit: int,
        account_ids: list[str] | None = None,
    ) -> list[Any]:
        del self, user_id, start_date, end_date, limit, account_ids
        return [local_transfer]

    monkeypatch.setattr(settings, "enable_unified_transaction_view", True)
    monkeypatch.setattr(UnifiedTransactionService, "_load_local_rows", _local_rows)

    result = await handle_transaction_list(
        provider,  # type: ignore[arg-type]
        _contract(query),
        account_id="acc_1",
        account_ids=["acc_1"],
        user_id="user_1",
        language="en",
    )

    assert result.items is not None
    assert len(result.items) == 1
    assert result.items[0].description == "Transfer to Tolu Adebayo"
    assert result.items[0].metadata is not None
    assert result.items[0].metadata["unified_source"] == "local"
    assert result.items[0].metadata["status"] == "failed"
