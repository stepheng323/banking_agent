import json
import time

import pytest

from banking.support.context_manager import SupportContextManager


class _RedisStub:
    def __init__(self, payload: dict[str, object]) -> None:
        self.values = {"support_context:user-1": json.dumps(payload)}
        self.setex_calls: list[tuple[str, int, str]] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.setex_calls.append((key, ttl, value))
        self.values[key] = value


@pytest.mark.asyncio
async def test_support_context_get_prunes_expired_ephemeral_fields() -> None:
    redis = _RedisStub(
        {
            "last_transaction_ref": "tx-last",
            "pending_reference": {
                "source": "recent_batch",
                "candidates": [],
                "reminder": "Reply with 1 or 2.",
                "intent": "receipt_request",
                "expires_at_ts": time.time() - 1,
            },
            "receipt_thread_state": {
                "async_group_id": "group-1",
                "candidates": [],
                "served_transaction_ids": [],
                "remaining_transaction_ids": [],
                "last_selector_result_ids": [],
                "last_served_transaction_ids": [],
                "reminder": "Reply with 1 or 2.",
                "expires_at_ts": time.time() - 1,
            },
        }
    )

    context = await SupportContextManager(redis).get("user-1")

    assert context.last_transaction_ref == "tx-last"
    assert context.pending_reference is None
    assert context.receipt_thread_state is None
    assert redis.setex_calls
    saved = json.loads(redis.values["support_context:user-1"])
    assert saved["pending_reference"] is None
    assert saved["receipt_thread_state"] is None
