import asyncio
import time
from datetime import date, timedelta
from typing import Any

import pytest

from apps.core.src.agent.graphs.query.handlers.transactions import handle_transaction_list
from apps.core.src.agent.graphs.query.models import (
    Filters,
    NormalizedQuery,
    QueryExecutionContract,
    QueryIntent,
    TimeRange,
)
from apps.core.src.agent.graphs.query.services.fetch import build_cache_fingerprint, build_cache_scope_fingerprint


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


def _query(filters: Filters) -> NormalizedQuery:
    today = date(2026, 3, 4)
    return NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today - timedelta(days=7), end=today),
        filters=filters,
    )


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
        QueryExecutionContract.from_normalized_query(query),
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
        QueryExecutionContract.from_normalized_query(query),
        "acc_1",
        ["acc_1"],
        continuation_type="filter_delta",
        continuation_delta_type="filter",
        session_cache={
            "cached_transactions": [{"id": "stale", "narration": "Old", "amount": 1, "date": "2026-03-01", "type": "debit"}],
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
    query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today - timedelta(days=1), end=today),
        filters=Filters(transaction_type="credit"),
    )
    provider = _Provider(
        transactions=[
            {"id": "4", "narration": "Salary", "amount": 90000, "date": "2026-03-03", "type": "credit"}
        ]
    )
    cached_query = _query(Filters(transaction_type="credit"))
    scope_fingerprint = build_cache_scope_fingerprint(query, "acc_1", ["acc_1"])

    result = await handle_transaction_list(
        provider,  # type: ignore[arg-type]
        QueryExecutionContract.from_normalized_query(query),
        "acc_1",
        ["acc_1"],
        continuation_type="time_delta",
        continuation_delta_type="time",
        session_cache={
            "cached_transactions": [{"id": "x", "narration": "cached", "amount": 10, "date": "2026-03-03", "type": "credit"}],
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
        transactions=[
            {"id": "4", "narration": "Salary", "amount": 90000, "date": "2026-03-03", "type": "credit"}
        ]
    )
    narrower_query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 3), end=date(2026, 3, 4)),
        filters=Filters(transaction_type="credit"),
    )
    scope_fingerprint = build_cache_scope_fingerprint(query, "acc_1", ["acc_1"])

    result = await handle_transaction_list(
        provider,  # type: ignore[arg-type]
        QueryExecutionContract.from_normalized_query(query),
        "acc_1",
        ["acc_1"],
        continuation_type="time_delta",
        continuation_delta_type="time",
        session_cache={
            "cached_transactions": [{"id": "x", "narration": "cached", "amount": 10, "date": "2026-03-03", "type": "credit"}],
            "cache_fetched_at": time.time() - 5,
            "cache_scope_fingerprint": scope_fingerprint,
            "cache_window_start": narrower_query.time_range.start.isoformat(),
            "cache_window_end": narrower_query.time_range.end.isoformat(),
        },
    )

    assert provider.calls == 1
    assert result.cache_reused is False


@pytest.mark.asyncio
async def test_multi_account_fetch_runs_concurrently() -> None:
    provider = _ConcurrentProvider()
    query = NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 4)),
    )

    result = await handle_transaction_list(
        provider,  # type: ignore[arg-type]
        QueryExecutionContract.from_normalized_query(query),
        "acc_1",
        ["acc_1", "acc_2", "acc_3"],
        continuation_type=None,
        continuation_delta_type=None,
    )

    assert provider.calls == 3
    assert provider.max_in_flight > 1
    assert result.cache_reused is False
