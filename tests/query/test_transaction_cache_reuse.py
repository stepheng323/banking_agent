import time
from datetime import date, timedelta
from typing import Any

import pytest

from apps.core.src.agent.graphs.query.handlers.transactions import handle_transaction_list
from apps.core.src.agent.graphs.query.models import Filters, NormalizedQuery, QueryIntent, TimeRange
from apps.core.src.agent.graphs.query.services.fetch import build_cache_fingerprint


class _Provider:
    def __init__(self, transactions: list[dict[str, Any]]) -> None:
        self.transactions = transactions
        self.calls = 0

    async def get_transactions(self, account_id: str, start_date: str, end_date: str, limit: int = 100) -> list[dict[str, Any]]:
        del account_id, start_date, end_date, limit
        self.calls += 1
        return list(self.transactions)


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

    result = await handle_transaction_list(
        provider,  # type: ignore[arg-type]
        query,
        "acc_1",
        ["acc_1"],
        continuation_type="filter_delta",
        continuation_delta_type="filter",
        session_cache={
            "cached_transactions": cached_transactions,
            "cache_fetched_at": time.time() - 5,
            "cache_fingerprint": fingerprint,
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

    result = await handle_transaction_list(
        provider,  # type: ignore[arg-type]
        query,
        "acc_1",
        ["acc_1"],
        continuation_type="filter_delta",
        continuation_delta_type="filter",
        session_cache={
            "cached_transactions": [{"id": "stale", "narration": "Old", "amount": 1, "date": "2026-03-01", "type": "debit"}],
            "cache_fetched_at": time.time() - 200,
            "cache_fingerprint": fingerprint,
        },
    )

    assert provider.calls == 1
    assert result.cache_reused is False


@pytest.mark.asyncio
async def test_time_delta_never_reuses_cache_even_when_fresh() -> None:
    query = _query(Filters(transaction_type="credit"))
    provider = _Provider(
        transactions=[
            {"id": "4", "narration": "Salary", "amount": 90000, "date": "2026-03-03", "type": "credit"}
        ]
    )
    fingerprint = build_cache_fingerprint(query, "acc_1", ["acc_1"])

    result = await handle_transaction_list(
        provider,  # type: ignore[arg-type]
        query,
        "acc_1",
        ["acc_1"],
        continuation_type="filter_delta",
        continuation_delta_type="time",
        session_cache={
            "cached_transactions": [{"id": "x", "narration": "cached", "amount": 10, "date": "2026-03-03", "type": "credit"}],
            "cache_fetched_at": time.time() - 5,
            "cache_fingerprint": fingerprint,
        },
    )

    assert provider.calls == 1
    assert result.cache_reused is False
