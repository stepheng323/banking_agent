"""Typed contracts shared by async transaction group services."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, Protocol, TypedDict

from shared.messaging.body_blocks import MessageDocument

ASYNC_GROUP_TTL_SECONDS = 3600


class AsyncGroupSummaryResult(TypedDict):
    text: str
    stage: Literal["initial", "final"]
    actionable_payload: NotRequired[dict[str, Any]]
    body_blocks: NotRequired[MessageDocument]


class RecentBatchLeg(TypedDict):
    index: int
    transaction_id: str | None
    task_type: str
    amount: float | None
    recipient_name: str | None
    recipient_resolved_name: str | None
    recipient_label: str | None
    bank_display: str | None
    account_display: str | None
    final_status: Literal["success", "processing", "failed"]
    error_message: str | None
    failure_category: str | None
    receipt_allowed: bool


class RecentBatchReference(TypedDict):
    async_group_id: str
    stored_at_ts: int
    legs: list[RecentBatchLeg]


class AsyncGroupRedis(Protocol):
    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> Any: ...

    async def get(self, key: str) -> Any: ...

    async def hset(self, key: str, field: str, value: str) -> Any: ...

    async def expire(self, key: str, ttl: int) -> Any: ...

    async def hlen(self, key: str) -> int: ...

    async def hgetall(self, key: str) -> dict[Any, Any]: ...


__all__ = [
    "ASYNC_GROUP_TTL_SECONDS",
    "AsyncGroupRedis",
    "AsyncGroupSummaryResult",
    "RecentBatchLeg",
    "RecentBatchReference",
]
