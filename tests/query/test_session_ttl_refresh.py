import json
from datetime import date
from time import time

import pytest

from banking.transactions.query.models.domain import QueryExecutionContract, QueryIntent, QueryIR, TimeRange
from banking.transactions.query.models.extraction import PendingClarificationState, QueryExtractionResult
from banking.transactions.query.session import SESSION_TTL, QuerySessionManager


def _query_ir(**kwargs: object) -> QueryIR:
    fallback_day = date(2026, 3, 28)
    defaults: dict[str, object] = {
        "intent": QueryIntent.TRANSACTION_LIST,
        "time_range": TimeRange(start=fallback_day, end=fallback_day),
    }
    defaults.update(kwargs)
    return QueryIR(**defaults)


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


def _contract(query: QueryIR) -> QueryExecutionContract:
    return QueryExecutionContract.from_query_ir(query)


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
                },
            }
        )
    )
    manager = QuerySessionManager(redis)  # type: ignore[arg-type]

    loaded = await manager.load(key)

    assert isinstance(loaded, dict)
    assert loaded["session_active"] is False
    assert "query_result" not in loaded
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
async def test_legacy_successful_result_state_is_not_restored_from_session_storage() -> None:
    redis = _RedisStoreStub()
    manager = QuerySessionManager(redis)  # type: ignore[arg-type]
    key = "query:session:2348000000003"

    await manager.save(
        key,
        {
            "session_active": True,
            "query_result": {
                "summary_text": "summary",
                "items": [{"description": "item", "amount": 1000, "date": "2026-03-07"}],
            },
            "query_frames": [{"frame_id": "qf_1"}],
            "selected_item_index": 0,
            "selected_payload": {"selection_kind": "transaction"},
            "cached_transactions": [{"id": "tx-1"}],
        },
    )
    loaded = await manager.load(key)

    assert isinstance(loaded, dict)
    assert "query_result" not in loaded
    assert "query_frames" not in loaded
    assert "selected_item_index" not in loaded
    assert "selected_payload" not in loaded
    assert "cached_transactions" not in loaded


@pytest.mark.asyncio
async def test_pending_clarification_round_trips_through_session_storage() -> None:
    redis = _RedisStoreStub()
    manager = QuerySessionManager(redis)  # type: ignore[arg-type]
    key = "query:session:2348000000005"

    pending = PendingClarificationState(
        original_query="How much did I spend last",
        current_intent=QueryIntent.ANALYTICS_SUMMARY,
        original_extraction=QueryExtractionResult(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            raw_query="How much did I spend last",
        ),
        resolver_message="What time period did you mean by last?",
    )

    await manager.save(
        key,
        {
            "session_active": True,
            "pending_clarification": pending,
            "query_contract": _contract(
                _query_ir(
                    intent=QueryIntent.ANALYTICS_SUMMARY,
                    time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19), granularity="week"),
                )
            ),
        },
    )
    loaded = await manager.load(key)

    assert isinstance(loaded, dict)
    restored_pending = loaded.get("pending_clarification")
    assert isinstance(restored_pending, PendingClarificationState)
    assert restored_pending.original_query == "How much did I spend last"
    assert restored_pending.resolver_message == "What time period did you mean by last?"
    assert isinstance(loaded.get("query_contract"), QueryExecutionContract)


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
