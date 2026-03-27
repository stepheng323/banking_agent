import json
from datetime import date
from time import time

import pytest

from apps.core.src.agent.graphs.query.models import (
    NormalizedQuery,
    QueryExecutionContract,
    QueryFrame,
    QueryFrameFacts,
    QueryIntent,
    QueryResult,
    QueryResultItem,
    TimeRange,
)
from apps.core.src.agent.graphs.query.session import SESSION_TTL, QuerySessionManager


class _RedisStub:
    def __init__(self, payload: str | None) -> None:
        self.payload = payload
        self.expire_calls: list[tuple[str, int]] = []

    async def get(self, key: str) -> str | None:
        del key
        return self.payload

    async def expire(self, key: str, ttl: int) -> bool:
        self.expire_calls.append((key, ttl))
        return True


class _RedisExpireFails(_RedisStub):
    async def expire(self, key: str, ttl: int) -> bool:
        self.expire_calls.append((key, ttl))
        raise RuntimeError("boom")


class _RedisStoreStub(_RedisStub):
    def __init__(self) -> None:
        super().__init__(None)
        self.saved: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.saved.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        del ex
        self.saved[key] = value
        return True

    async def delete(self, key: str) -> int:
        self.saved.pop(key, None)
        return 1


@pytest.mark.asyncio
async def test_load_refreshes_ttl_on_success() -> None:
    key = "query:session:2348000000000"
    redis = _RedisStub(json.dumps({"session_active": True, "timestamp": time(), "current_page": 1}))
    manager = QuerySessionManager(redis)  # type: ignore[arg-type]

    loaded = await manager.load(key)

    assert isinstance(loaded, dict)
    assert loaded.get("session_active") is True
    assert redis.expire_calls == [(key, SESSION_TTL)]


@pytest.mark.asyncio
async def test_load_does_not_refresh_ttl_when_session_missing() -> None:
    redis = _RedisStub(None)
    manager = QuerySessionManager(redis)  # type: ignore[arg-type]

    loaded = await manager.load("query:session:2348000000001")

    assert loaded is None
    assert redis.expire_calls == []


@pytest.mark.asyncio
async def test_load_disarms_stale_query_session_without_refresh() -> None:
    key = "query:session:2348000000004"
    redis = _RedisStub(
        json.dumps(
            {
                "session_active": True,
                "timestamp": time() - SESSION_TTL - 10,
                "current_page": 2,
                "query_contract": {
                    "intent": "transaction_list",
                    "time_start": "2026-03-01",
                    "time_end": "2026-03-14",
                    "timezone": "Africa/Lagos",
                    "normalized_query": {
                        "intent": "transaction_list",
                        "time_range": {"start": "2026-03-01", "end": "2026-03-14", "granularity": "day"},
                        "accounts_scope": "all",
                    },
                },
            }
        )
    )
    manager = QuerySessionManager(redis)  # type: ignore[arg-type]

    loaded = await manager.load(key)

    assert isinstance(loaded, dict)
    assert loaded["session_active"] is False
    assert loaded["query_result"] is None
    assert loaded.get("pending_clarification") is None
    assert redis.expire_calls == []


@pytest.mark.asyncio
async def test_load_expire_failure_is_non_fatal() -> None:
    key = "query:session:2348000000002"
    redis = _RedisExpireFails(json.dumps({"session_active": True}))
    manager = QuerySessionManager(redis)  # type: ignore[arg-type]

    loaded = await manager.load(key)

    assert isinstance(loaded, dict)
    assert loaded.get("session_active") is True
    assert redis.expire_calls == [(key, SESSION_TTL)]


@pytest.mark.asyncio
async def test_query_result_interpretation_round_trips_through_session_storage() -> None:
    redis = _RedisStoreStub()
    manager = QuerySessionManager(redis)  # type: ignore[arg-type]
    key = "query:session:2348000000003"

    query_result = QueryResult(
        summary_text="summary",
        items=[QueryResultItem(description="item", amount=1000, date=date(2026, 3, 7))],
        interpretation={
            "intent": "time_comparison",
            "time_window": {"start": "2026-03-01", "end": "2026-03-07", "timezone": "Africa/Lagos"},
        },
    )

    await manager.save(
        key,
        {
            "session_active": True,
            "query_result": query_result,
        },
    )
    loaded = await manager.load(key)

    assert isinstance(loaded, dict)
    restored_result = loaded.get("query_result")
    assert isinstance(restored_result, QueryResult)
    assert restored_result.interpretation is not None
    assert restored_result.interpretation["intent"] == "time_comparison"


@pytest.mark.asyncio
async def test_query_frames_round_trip_through_session_storage() -> None:
    redis = _RedisStoreStub()
    manager = QuerySessionManager(redis)  # type: ignore[arg-type]
    key = "query:session:2348000000005"

    frame = QueryFrame(
        frame_id="qf_1",
        turn_index=1,
        query_contract=QueryExecutionContract.from_normalized_query(
            NormalizedQuery(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19), granularity="week"),
            )
        ),
        summary_text="You spent ₦60,000 this week.",
        interpretation={"intent": "analytics_summary"},
        facts=QueryFrameFacts(metric_kind="amount", amount=60000.0, count=2, direction="debit"),
    )

    await manager.save(
        key,
        {
            "session_active": True,
            "query_frames": [frame],
        },
    )
    loaded = await manager.load(key)

    assert isinstance(loaded, dict)
    restored_frames = loaded.get("query_frames")
    assert isinstance(restored_frames, list)
    assert len(restored_frames) == 1
    assert isinstance(restored_frames[0], QueryFrame)
    assert restored_frames[0].facts.amount == 60000.0


@pytest.mark.asyncio
async def test_cache_scope_metadata_round_trips_through_session_storage() -> None:
    redis = _RedisStoreStub()
    manager = QuerySessionManager(redis)  # type: ignore[arg-type]
    key = "query:session:2348000000006"

    await manager.save(
        key,
        {
            "session_active": True,
            "cache_scope_fingerprint": "scope-123",
            "cache_window_start": "2026-03-01",
            "cache_window_end": "2026-03-31",
        },
    )
    loaded = await manager.load(key)

    assert isinstance(loaded, dict)
    assert loaded["cache_scope_fingerprint"] == "scope-123"
    assert loaded["cache_window_start"] == "2026-03-01"
    assert loaded["cache_window_end"] == "2026-03-31"
