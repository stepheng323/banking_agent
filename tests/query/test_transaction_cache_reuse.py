import asyncio
import time
from datetime import date, timedelta
from typing import Any

import pytest

from banking.transactions.query.handlers.transactions import handle_transaction_list
from banking.transactions.query.models.domain import (
    Filters,
    QueryIntent,
    QueryRequest,
    TimeRange,
)
from banking.transactions.query.services.fetching.fetch import (
    apply_time_window,
    build_cache_fingerprint,
    build_cache_scope_fingerprint,
    decide_transaction_cache_reuse,
)
from shared.config.settings import settings
from tests.query.factories import make_query_request


def _query_ir(**kwargs: object) -> QueryRequest:
    fallback_day = date(2026, 3, 4)
    defaults: dict[str, object] = {
        "intent": QueryIntent.TRANSACTION_LIST,
        "time_range": TimeRange(start=fallback_day, end=fallback_day),
    }
    defaults.update(kwargs)
    return make_query_request(**defaults)


class _Provider:
    def __init__(self, transactions: list[dict[str, Any]]) -> None:
        self.transactions = transactions
        self.calls = 0

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
        self.calls += 1
        return list(self.transactions)


class _ConcurrentProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0

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
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            return []
        finally:
            self.in_flight -= 1


def _query(filters: Filters) -> QueryRequest:
    today = date(2026, 3, 4)
    return _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today - timedelta(days=7), end=today),
        filters=filters,
    )


def _contract(query: QueryRequest) -> QueryRequest:
    return (query)


@pytest.mark.asyncio
async def test_filter_delta_reuses_fresh_cached_transactions() -> None:
    query = _query(Filters(transaction_type="credit"))
    cached_transactions = [
        {"id": "1", "narration": "Salary", "amount": 100000, "date": "2026-03-03", "type": "credit"},
        {"id": "2", "narration": "Transfer to Ada", "amount": 5000, "date": "2026-03-03", "type": "debit"},
    ]
    provider = _Provider(transactions=[])
    fingerprint = build_cache_fingerprint(query, "acc_1", ["acc_1"])
    scope_fingerprint = build_cache_scope_fingerprint(query, "acc_1", ["acc_1"])

    result = await handle_transaction_list(
        provider,  # type: ignore[arg-type]
        _contract(query),
        "acc_1",
        ["acc_1"],
        continuation_type="filter_delta",
        continuation_delta_type="filter",
        session_cache={
            "cached_transactions": cached_transactions,
            "cache_fetched_at": time.time() - 5,
            "cache_fingerprint": fingerprint,
            "cache_scope_fingerprint": scope_fingerprint,
            "cache_window_start": query.time_range.start.isoformat(),
            "cache_window_end": query.time_range.end.isoformat(),
        },
    )

    assert provider.calls == 0
    assert result.cache_reused is True
    assert result.items is not None
    assert len(result.items) == 1
    assert result.items[0].metadata is not None
    assert result.items[0].metadata["type"] == "credit"


@pytest.mark.asyncio
async def test_filter_delta_does_not_reuse_stale_cache() -> None:
    query = _query(Filters(transaction_type="debit"))
    provider = _Provider(
        transactions=[
            {"id": "3", "narration": "Transfer to Tolu", "amount": 7000, "date": "2026-03-03", "type": "debit"}
        ]
    )
    fingerprint = build_cache_fingerprint(query, "acc_1", ["acc_1"])
    scope_fingerprint = build_cache_scope_fingerprint(query, "acc_1", ["acc_1"])

    result = await handle_transaction_list(
        provider,  # type: ignore[arg-type]
        _contract(query),
        "acc_1",
        ["acc_1"],
        continuation_type="filter_delta",
        continuation_delta_type="filter",
        session_cache={
            "cached_transactions": [
                {"id": "stale", "narration": "Old", "amount": 1, "date": "2026-03-01", "type": "debit"}
            ],
            "cache_fetched_at": time.time() - 200,
            "cache_fingerprint": fingerprint,
            "cache_scope_fingerprint": scope_fingerprint,
            "cache_window_start": query.time_range.start.isoformat(),
            "cache_window_end": query.time_range.end.isoformat(),
        },
    )

    assert provider.calls == 1
    assert result.cache_reused is False


@pytest.mark.asyncio
async def test_time_delta_reuses_fresh_subset_cache() -> None:
    today = date(2026, 3, 4)
    query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today - timedelta(days=1), end=today),
        filters=Filters(transaction_type="credit"),
    )
    provider = _Provider(
        transactions=[{"id": "4", "narration": "Salary", "amount": 90000, "date": "2026-03-03", "type": "credit"}]
    )
    cached_query = _query(Filters(transaction_type="credit"))
    scope_fingerprint = build_cache_scope_fingerprint(query, "acc_1", ["acc_1"])

    result = await handle_transaction_list(
        provider,  # type: ignore[arg-type]
        _contract(query),
        "acc_1",
        ["acc_1"],
        continuation_type="time_delta",
        continuation_delta_type="time",
        session_cache={
            "cached_transactions": [
                {"id": "x", "narration": "cached", "amount": 10, "date": "2026-03-03", "type": "credit"}
            ],
            "cache_fetched_at": time.time() - 5,
            "cache_scope_fingerprint": scope_fingerprint,
            "cache_window_start": cached_query.time_range.start.isoformat(),
            "cache_window_end": cached_query.time_range.end.isoformat(),
        },
    )

    assert provider.calls == 0
    assert result.cache_reused is True


@pytest.mark.asyncio
async def test_time_delta_does_not_reuse_cache_for_wider_window() -> None:
    query = _query(Filters(transaction_type="credit"))
    provider = _Provider(
        transactions=[{"id": "4", "narration": "Salary", "amount": 90000, "date": "2026-03-03", "type": "credit"}]
    )
    narrower_query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 3), end=date(2026, 3, 4)),
        filters=Filters(transaction_type="credit"),
    )
    scope_fingerprint = build_cache_scope_fingerprint(query, "acc_1", ["acc_1"])

    result = await handle_transaction_list(
        provider,  # type: ignore[arg-type]
        _contract(query),
        "acc_1",
        ["acc_1"],
        continuation_type="time_delta",
        continuation_delta_type="time",
        session_cache={
            "cached_transactions": [
                {"id": "x", "narration": "cached", "amount": 10, "date": "2026-03-03", "type": "credit"}
            ],
            "cache_fetched_at": time.time() - 5,
            "cache_scope_fingerprint": scope_fingerprint,
            "cache_window_start": narrower_query.time_range.start.isoformat(),
            "cache_window_end": narrower_query.time_range.end.isoformat(),
        },
    )

    assert provider.calls == 1
    assert result.cache_reused is False


def test_apply_time_window_includes_only_transactions_within_inclusive_bounds() -> None:
    transactions = [
        {"id": "tx-before", "date": "2026-03-01", "amount": 1000},
        {"id": "tx-start", "date": "2026-03-02", "amount": 2000},
        {"id": "tx-middle", "date": "2026-03-03", "amount": 3000},
        {"id": "tx-end", "date": "2026-03-04", "amount": 4000},
        {"id": "tx-after", "date": "2026-03-05", "amount": 5000},
    ]

    scoped = apply_time_window(
        transactions,
        window_start=date(2026, 3, 2),
        window_end=date(2026, 3, 4),
    )

    assert [transaction["id"] for transaction in scoped] == ["tx-start", "tx-middle", "tx-end"]


def test_decide_transaction_cache_reuse_returns_exact_for_filter_delta_same_envelope() -> None:
    decision = decide_transaction_cache_reuse(
        continuation_type="filter_delta",
        continuation_delta_type="filter",
        cached_transactions=[{"id": "tx-1"}],
        cache_age_seconds=5,
        max_cache_age_seconds=90,
        cached_fingerprint="fp-1",
        current_fingerprint="fp-1",
        cached_scope_fingerprint="scope-1",
        current_scope_fingerprint="scope-1",
        cache_window_start="2026-03-01",
        cache_window_end="2026-03-31",
        current_window_start="2026-03-01",
        current_window_end="2026-03-31",
    )

    assert decision.can_reuse is True
    assert decision.strategy == "exact"


def test_decide_transaction_cache_reuse_returns_time_subset_for_narrower_window() -> None:
    decision = decide_transaction_cache_reuse(
        continuation_type="time_delta",
        continuation_delta_type="time",
        cached_transactions=[{"id": "tx-1"}],
        cache_age_seconds=5,
        max_cache_age_seconds=90,
        cached_fingerprint="fp-1",
        current_fingerprint="fp-2",
        cached_scope_fingerprint="scope-1",
        current_scope_fingerprint="scope-1",
        cache_window_start="2026-03-01",
        cache_window_end="2026-03-31",
        current_window_start="2026-03-10",
        current_window_end="2026-03-20",
    )

    assert decision.can_reuse is True
    assert decision.strategy == "time_subset"


def test_decide_transaction_cache_reuse_rejects_wider_time_window() -> None:
    decision = decide_transaction_cache_reuse(
        continuation_type="time_delta",
        continuation_delta_type="time",
        cached_transactions=[{"id": "tx-1"}],
        cache_age_seconds=5,
        max_cache_age_seconds=90,
        cached_fingerprint="fp-1",
        current_fingerprint="fp-2",
        cached_scope_fingerprint="scope-1",
        current_scope_fingerprint="scope-1",
        cache_window_start="2026-03-10",
        cache_window_end="2026-03-20",
        current_window_start="2026-03-01",
        current_window_end="2026-03-31",
    )

    assert decision.can_reuse is False
    assert decision.strategy == "none"


def test_cache_fingerprints_change_between_bank_and_unified_views(monkeypatch: pytest.MonkeyPatch) -> None:
    query = _query(Filters(transaction_type="credit"))

    monkeypatch.setattr(settings, "enable_unified_transaction_view", False)
    bank_fingerprint = build_cache_fingerprint(query, "acc_1", ["acc_1"], user_id="user_1")
    bank_scope_fingerprint = build_cache_scope_fingerprint(query, "acc_1", ["acc_1"], user_id="user_1")

    monkeypatch.setattr(settings, "enable_unified_transaction_view", True)
    unified_fingerprint = build_cache_fingerprint(query, "acc_1", ["acc_1"], user_id="user_1")
    unified_scope_fingerprint = build_cache_scope_fingerprint(query, "acc_1", ["acc_1"], user_id="user_1")

    assert unified_fingerprint != bank_fingerprint
    assert unified_scope_fingerprint != bank_scope_fingerprint


@pytest.mark.asyncio
async def test_multi_account_fetch_runs_concurrently() -> None:
    provider = _ConcurrentProvider()
    query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 4)),
    )

    result = await handle_transaction_list(
        provider,  # type: ignore[arg-type]
        _contract(query),
        "acc_1",
        ["acc_1", "acc_2", "acc_3"],
        continuation_type=None,
        continuation_delta_type=None,
    )

    assert provider.calls == 3
    assert provider.max_in_flight > 1
    assert result.cache_reused is False


@pytest.mark.asyncio
async def test_empty_transaction_list_summary_uses_zero_zero_range() -> None:
    provider = _Provider(transactions=[])
    query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 4), end=date(2026, 3, 4)),
    )

    result = await handle_transaction_list(
        provider,  # type: ignore[arg-type]
        _contract(query),
        "acc_1",
        ["acc_1", "acc_2"],
    )

    assert result.summary_text == "accounts:2|showing:0-0|total:0"
    assert result.items == []


@pytest.mark.asyncio
async def test_oldest_result_reference_reorders_transaction_list_before_limiting() -> None:
    provider = _Provider(
        transactions=[
            {"id": "tx-3", "narration": "Third", "amount": 3000, "date": "2026-03-03", "type": "debit"},
            {"id": "tx-2", "narration": "Second", "amount": 2000, "date": "2026-03-02", "type": "debit"},
            {"id": "tx-1", "narration": "First", "amount": 1000, "date": "2026-03-01", "type": "debit"},
        ]
    )
    query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 3)),
        filters=Filters(transaction_type="debit"),
        result_reference="oldest",
        result_limit=1,
    )

    result = await handle_transaction_list(
        provider,  # type: ignore[arg-type]
        _contract(query),
        "acc_1",
        ["acc_1"],
    )

    assert [item.id for item in result.items or []] == ["tx-1"]


@pytest.mark.asyncio
async def test_oldest_result_reference_applies_before_pagination() -> None:
    provider = _Provider(
        transactions=[
            {"id": "tx-3", "narration": "Third", "amount": 3000, "date": "2026-03-03", "type": "debit"},
            {"id": "tx-2", "narration": "Second", "amount": 2000, "date": "2026-03-02", "type": "debit"},
            {"id": "tx-1", "narration": "First", "amount": 1000, "date": "2026-03-01", "type": "debit"},
        ]
    )
    query = _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 3)),
        filters=Filters(transaction_type="debit"),
        result_reference="oldest",
    )

    result = await handle_transaction_list(
        provider,  # type: ignore[arg-type]
        _contract(query),
        "acc_1",
        ["acc_1"],
        current_page=1,
        page_size=1,
    )

    assert [item.id for item in result.items or []] == ["tx-2"]
