"""Shared async completion helpers for transaction-worker executors."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Literal, TypedDict

import redis.asyncio as redis

from shared.formatters.transaction_summary import format_multi_action_summary
from shared.queue.models import AsyncGroupMeta
from shared.utils.logging import get_logger

logger = get_logger(__name__)
ASYNC_GROUP_TTL_SECONDS = 3600
ASYNC_GROUP_TRANSACTION_META_PREFIX = "async-group:transaction-meta"
ASYNC_GROUP_LEGS_PREFIX = "async-group"


class AsyncGroupSummaryResult(TypedDict):
    text: str
    stage: Literal["initial", "final"]


def get_async_group_meta(message: dict[str, Any]) -> AsyncGroupMeta | None:
    raw = message.get("async_group")
    if not isinstance(raw, dict):
        return None
    group_id = raw.get("async_group_id")
    size = raw.get("async_group_size")
    kind = raw.get("async_group_kind")
    index = raw.get("async_group_index")
    if not isinstance(group_id, str) or not group_id.strip():
        return None
    if not isinstance(size, int) or size < 1:
        return None
    if kind not in {"single", "multi_transfer", "mixed_batch"}:
        return None
    if not isinstance(index, int) or index < 1:
        return None
    return {
        "async_group_id": group_id,
        "async_group_size": size,
        "async_group_kind": kind,
        "async_group_index": index,
    }


def is_grouped_async_message(message: dict[str, Any]) -> bool:
    meta = get_async_group_meta(message)
    return bool(meta and meta["async_group_size"] > 1)


def _group_legs_key(group_id: str) -> str:
    return f"{ASYNC_GROUP_LEGS_PREFIX}:{group_id}:legs"


def _group_finalized_key(group_id: str) -> str:
    return f"{ASYNC_GROUP_LEGS_PREFIX}:{group_id}:finalized"


def _group_initial_key(group_id: str) -> str:
    return f"{ASYNC_GROUP_LEGS_PREFIX}:{group_id}:initial"


def _transaction_meta_key(transaction_id: str) -> str:
    return f"{ASYNC_GROUP_TRANSACTION_META_PREFIX}:{transaction_id}"


async def _remember_transaction_group_meta(
    redis_client: redis.Redis,
    *,
    transaction_id: str | None,
    meta: AsyncGroupMeta,
) -> None:
    if not transaction_id:
        return
    await redis_client.set(
        _transaction_meta_key(transaction_id),
        json.dumps(meta),
        ex=ASYNC_GROUP_TTL_SECONDS,
    )


async def get_async_group_meta_for_transaction(
    redis_client: redis.Redis | None,
    *,
    transaction_id: str | None,
) -> AsyncGroupMeta | None:
    if redis_client is None or not transaction_id:
        return None
    raw = await redis_client.get(_transaction_meta_key(transaction_id))
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        decoded = json.loads(raw)
    except Exception:
        logger.warning("async_group_meta_decode_failed", transaction_id=transaction_id)
        return None
    if not isinstance(decoded, dict):
        return None
    return get_async_group_meta({"async_group": decoded})


def _is_terminal_status(status: str) -> bool:
    return status in {"success", "failed"}


async def _load_ordered_legs(redis_client: redis.Redis, *, legs_key: str, group_id: str) -> list[dict[str, Any]]:
    raw_legs = await redis_client.hgetall(legs_key)
    ordered: list[dict[str, Any]] = []
    for raw_index, raw_payload in sorted(raw_legs.items(), key=lambda item: int(item[0])):
        try:
            decoded = json.loads(raw_payload)
        except Exception:
            logger.warning("async_group_leg_decode_failed", group_id=group_id, index=raw_index)
            continue
        if not isinstance(decoded, dict):
            continue
        ordered.append(decoded)
    return ordered


def _all_legs_terminal(ordered: list[dict[str, Any]]) -> bool:
    if not ordered:
        return False
    statuses = []
    for leg in ordered:
        payload = leg.get("payload")
        if not isinstance(payload, dict):
            return False
        statuses.append(_normalize_final_status(str(payload.get("final_status") or "success")))
    return bool(statuses) and all(_is_terminal_status(status) for status in statuses)


async def record_group_leg_and_maybe_build_summary(
    redis_client: redis.Redis | None,
    *,
    message: dict[str, Any],
    task_type: str,
    payload: dict[str, Any],
    locale: str,
) -> AsyncGroupSummaryResult | None:
    meta = get_async_group_meta(message)
    if not meta or meta["async_group_size"] <= 1:
        return None
    if redis_client is None:
        logger.warning(
            "async_group_summary_skipped",
            reason="redis_unavailable",
            task_type=task_type,
            transaction_id=message.get("transaction_id"),
        )
        return None

    group_id = meta["async_group_id"]
    legs_key = _group_legs_key(group_id)
    initial_key = _group_initial_key(group_id)
    finalized_key = _group_finalized_key(group_id)
    field = str(meta["async_group_index"])
    await _remember_transaction_group_meta(
        redis_client,
        transaction_id=str(message.get("transaction_id") or "").strip() or None,
        meta=meta,
    )
    stored_leg = json.dumps(
        {
            "type": task_type,
            "payload": payload,
            "transaction_id": message.get("transaction_id"),
            "index": meta["async_group_index"],
        }
    )
    await redis_client.hset(legs_key, field, stored_leg)
    await redis_client.expire(legs_key, ASYNC_GROUP_TTL_SECONDS)

    current_count = await redis_client.hlen(legs_key)
    if current_count < meta["async_group_size"]:
        return None

    ordered = await _load_ordered_legs(redis_client, legs_key=legs_key, group_id=group_id)
    if not ordered:
        return None
    all_terminal = _all_legs_terminal(ordered)

    initial_sent = await redis_client.set(initial_key, "1", ex=ASYNC_GROUP_TTL_SECONDS, nx=True)
    if initial_sent:
        if all_terminal:
            await redis_client.set(finalized_key, "1", ex=ASYNC_GROUP_TTL_SECONDS)
            return {"text": build_group_summary(ordered, locale=locale), "stage": "final"}
        return {"text": build_group_summary(ordered, locale=locale), "stage": "initial"}

    if not all_terminal:
        return None

    finalized = await redis_client.set(finalized_key, "1", ex=ASYNC_GROUP_TTL_SECONDS, nx=True)
    if not finalized:
        return None
    return {"text": build_group_summary(ordered, locale=locale), "stage": "final"}


def build_group_summary(legs: list[dict[str, Any]], *, locale: str) -> str:
    pseudo_tasks = []
    for leg in legs:
        task_type = str(leg.get("type") or "")
        payload = leg.get("payload")
        if not isinstance(payload, dict):
            continue
        pseudo_tasks.append(SimpleNamespace(type=task_type, payload=payload))
    return format_multi_action_summary(pseudo_tasks, locale=locale)


def _normalize_final_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized in {"success", "successful", "confirmed", "completed"}:
        return "success"
    if normalized in {"pending", "processing", "queued"}:
        return "processing"
    if normalized in {"failed", "error"}:
        return "failed"
    return normalized or "success"
