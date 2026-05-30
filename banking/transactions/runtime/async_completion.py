"""Async completion helpers for transaction-worker executors and webhook follow-ups."""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from banking.presentation.formatters.multi_action_summary import format_multi_action_summary
from banking.transactions.runtime.async_group_recent_batch import (
    normalize_final_status,
    remember_group_target,
    store_recent_batch_reference,
)
from banking.transactions.runtime.async_group_types import (
    ASYNC_GROUP_TTL_SECONDS,
    AsyncGroupRedis,
    AsyncGroupSummaryResult,
)
from shared.money import money_to_json
from shared.queue.models import AsyncGroupMeta
from shared.utils.logging import get_logger

logger = get_logger(__name__)
ASYNC_GROUP_TRANSACTION_META_PREFIX = "async-group:transaction-meta"
ASYNC_GROUP_LEGS_PREFIX = "async-group"
ASYNC_GROUP_ACTION_BY_TYPE = {
    "transfer": "send_money",
    "airtime": "buy_airtime",
    "data": "buy_data",
}
ASYNC_GROUP_ACTIONABLE_PAYLOAD_KEYS = (
    "amount",
    "beneficiary_id",
    "recipient_name",
    "recipient_resolved_name",
    "recipient_phone",
    "target_phone",
    "phone_number",
    "recipient_account",
    "recipient_account_number",
    "recipient_bank_code",
    "recipient_bank_name",
    "resolved_from_saved_beneficiary",
    "source_bank_name",
    "source_account_id",
    "source_account_index",
    "source_account_number",
    "source_affinity_mode",
    "narration",
    "network",
    "plan_code",
    "plan_name",
    "final_status",
    "error_message",
    "failure_category",
)


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


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        serialized = money_to_json(value)
        if serialized is not None:
            return serialized
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


async def _remember_transaction_group_meta(
    redis_client: AsyncGroupRedis,
    *,
    transaction_id: str | None,
    meta: AsyncGroupMeta,
) -> None:
    if not transaction_id:
        return
    await redis_client.set(
        _transaction_meta_key(transaction_id),
        json.dumps(meta, default=_json_default),
        ex=ASYNC_GROUP_TTL_SECONDS,
    )


async def get_async_group_meta_for_transaction(
    redis_client: AsyncGroupRedis | None,
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


async def _load_ordered_legs(redis_client: AsyncGroupRedis, *, legs_key: str, group_id: str) -> list[dict[str, Any]]:
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
        statuses.append(normalize_final_status(str(payload.get("final_status") or "success")))
    return bool(statuses) and all(_is_terminal_status(status) for status in statuses)


async def record_group_leg_and_maybe_build_summary(
    redis_client: AsyncGroupRedis | None,
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
    await remember_group_target(redis_client, group_id=group_id, message=message)
    stored_leg = json.dumps(
        {
            "type": task_type,
            "payload": payload,
            "transaction_id": message.get("transaction_id"),
            "index": meta["async_group_index"],
        },
        default=_json_default,
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
        await store_recent_batch_reference(redis_client, group_id=group_id, ordered=ordered, message=message)
        actionable_payload = build_group_actionable_payload(ordered)
        if all_terminal:
            await redis_client.set(finalized_key, "1", ex=ASYNC_GROUP_TTL_SECONDS)
            result: AsyncGroupSummaryResult = {"text": build_group_summary(ordered, locale=locale), "stage": "final"}
            if actionable_payload:
                result["actionable_payload"] = actionable_payload
            return result
        result = {"text": build_group_summary(ordered, locale=locale), "stage": "initial"}
        if actionable_payload:
            result["actionable_payload"] = actionable_payload
        return result

    if not all_terminal:
        return None

    finalized = await redis_client.set(finalized_key, "1", ex=ASYNC_GROUP_TTL_SECONDS, nx=True)
    if not finalized:
        return None
    await store_recent_batch_reference(redis_client, group_id=group_id, ordered=ordered, message=message)
    result = {"text": build_group_summary(ordered, locale=locale), "stage": "final"}
    if actionable_payload := build_group_actionable_payload(ordered):
        result["actionable_payload"] = actionable_payload
    return result


def build_group_summary(legs: list[dict[str, Any]], *, locale: str) -> str:
    pseudo_tasks = []
    for leg in legs:
        task_type = str(leg.get("type") or "")
        payload = leg.get("payload")
        if not isinstance(payload, dict):
            continue
        pseudo_tasks.append(SimpleNamespace(type=task_type, payload=payload))
    return format_multi_action_summary(pseudo_tasks, locale=locale)


def build_group_actionable_payload(legs: list[dict[str, Any]]) -> dict[str, Any] | None:
    payloads: list[dict[str, Any]] = []
    for leg in legs:
        task_type = str(leg.get("type") or "").strip().lower()
        action = ASYNC_GROUP_ACTION_BY_TYPE.get(task_type)
        payload = leg.get("payload")
        if not action or not isinstance(payload, dict):
            continue

        task_payload: dict[str, Any] = {
            "task_id": f"async_group_{leg.get('index') or len(payloads) + 1}",
            "task_type": task_type,
            "action": action,
        }
        transaction_id = leg.get("transaction_id")
        if transaction_id:
            task_payload["transaction_id"] = str(transaction_id)

        for key in ASYNC_GROUP_ACTIONABLE_PAYLOAD_KEYS:
            value = payload.get(key)
            if value is not None and value != "":
                task_payload[key] = value

        if task_type == "airtime" and not task_payload.get("recipient_phone"):
            phone = task_payload.get("target_phone") or task_payload.get("phone_number")
            if phone:
                task_payload["recipient_phone"] = phone
        if task_type == "data" and not task_payload.get("target_phone"):
            phone = task_payload.get("recipient_phone") or task_payload.get("phone_number")
            if phone:
                task_payload["target_phone"] = phone

        task_payload.pop("phone_number", None)
        payloads.append(task_payload)

    if not payloads:
        return None
    if len(payloads) == 1:
        return payloads[0]
    return {
        "task_type": "batch",
        "task_ids": [payload["task_id"] for payload in payloads],
        "task_types": [payload["task_type"] for payload in payloads],
        "tasks": payloads,
    }
