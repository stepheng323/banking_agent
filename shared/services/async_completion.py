"""Shared async completion helpers for transaction-worker executors and webhook follow-ups."""

from __future__ import annotations

import json
import re
import time
from types import SimpleNamespace
from typing import Any, Literal, NotRequired, Protocol, TypedDict, cast

from shared.formatters.transaction_summary import format_multi_action_summary
from shared.queue.models import AsyncGroupMeta
from shared.utils.logging import get_logger

logger = get_logger(__name__)
ASYNC_GROUP_TTL_SECONDS = 3600
ASYNC_GROUP_TRANSACTION_META_PREFIX = "async-group:transaction-meta"
ASYNC_GROUP_LEGS_PREFIX = "async-group"
ASYNC_GROUP_TARGET_PREFIX = "async-group:target"
ASYNC_GROUP_RECENT_BATCH_PREFIX = "async-group:recent-batch"
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
    "narration",
    "network",
    "plan_code",
    "plan_name",
)


class AsyncGroupSummaryResult(TypedDict):
    text: str
    stage: Literal["initial", "final"]
    actionable_payload: NotRequired[dict[str, Any]]


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


def _group_target_key(group_id: str) -> str:
    return f"{ASYNC_GROUP_TARGET_PREFIX}:{group_id}"


def _transaction_meta_key(transaction_id: str) -> str:
    return f"{ASYNC_GROUP_TRANSACTION_META_PREFIX}:{transaction_id}"


def _recent_batch_key(identity: str) -> str:
    return f"{ASYNC_GROUP_RECENT_BATCH_PREFIX}:{identity}"


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
        json.dumps(meta),
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


def _decode_json_mapping(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        decoded = json.loads(raw)
    except Exception:
        return None
    return decoded if isinstance(decoded, dict) else None


def _is_terminal_status(status: str) -> bool:
    return status in {"success", "failed"}


def _resolve_recent_batch_identity(message: dict[str, Any]) -> str | None:
    for key in ("channel_identity", "phone_number"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def _remember_group_target(redis_client: AsyncGroupRedis, *, group_id: str, message: dict[str, Any]) -> None:
    identity = _resolve_recent_batch_identity(message)
    if identity is None:
        return
    payload = {
        "identity": identity,
        "channel": str(message.get("channel") or "whatsapp"),
        "channel_identity": message.get("channel_identity"),
        "phone_number": message.get("phone_number"),
    }
    await redis_client.set(_group_target_key(group_id), json.dumps(payload), ex=ASYNC_GROUP_TTL_SECONDS)


async def _load_group_target(redis_client: AsyncGroupRedis, *, group_id: str) -> dict[str, Any] | None:
    return _decode_json_mapping(await redis_client.get(_group_target_key(group_id)))


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
        statuses.append(_normalize_final_status(str(payload.get("final_status") or "success")))
    return bool(statuses) and all(_is_terminal_status(status) for status in statuses)


def _coerce_amount(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        compact = re.sub(r"[^0-9.]", "", value)
        if not compact:
            return None
        try:
            return float(compact)
        except ValueError:
            return None
    return None


def _build_recent_batch_leg(leg: dict[str, Any]) -> RecentBatchLeg | None:
    task_type = str(leg.get("type") or "").strip()
    payload = leg.get("payload")
    if not task_type or not isinstance(payload, dict):
        return None

    normalized_status = _normalize_final_status(str(payload.get("final_status") or "success"))
    recipient_name = (
        str(payload.get("recipient_name")).strip()
        if isinstance(payload.get("recipient_name"), str)
        else None
    )
    recipient_resolved_name = (
        str(payload.get("recipient_resolved_name")).strip()
        if isinstance(payload.get("recipient_resolved_name"), str)
        else None
    )
    recipient_label = recipient_resolved_name or recipient_name
    bank_display = (
        str(payload.get("recipient_bank_name")).strip()
        if isinstance(payload.get("recipient_bank_name"), str)
        else (str(payload.get("network")).strip() if isinstance(payload.get("network"), str) else None)
    )
    account_display = (
        str(payload.get("recipient_account")).strip()
        if isinstance(payload.get("recipient_account"), str)
        else (
            str(payload.get("phone_number")).strip()
            if isinstance(payload.get("phone_number"), str)
            else (str(payload.get("target_phone")).strip() if isinstance(payload.get("target_phone"), str) else None)
        )
    )
    return {
        "index": int(leg.get("index") or 0),
        "transaction_id": str(leg.get("transaction_id")).strip() if leg.get("transaction_id") is not None else None,
        "task_type": task_type,
        "amount": _coerce_amount(payload.get("amount")),
        "recipient_name": recipient_name,
        "recipient_resolved_name": recipient_resolved_name,
        "recipient_label": recipient_label,
        "bank_display": bank_display,
        "account_display": account_display,
        "final_status": cast(Literal["success", "processing", "failed"], normalized_status),
        "receipt_allowed": task_type == "transfer" and normalized_status == "success",
    }


async def _store_recent_batch_reference(
    redis_client: AsyncGroupRedis,
    *,
    group_id: str,
    ordered: list[dict[str, Any]],
    message: dict[str, Any],
) -> None:
    identity = _resolve_recent_batch_identity(message)
    if identity is None:
        stored_target = await _load_group_target(redis_client, group_id=group_id)
        if stored_target is not None:
            identity = str(stored_target.get("identity") or "").strip() or None
    if identity is None:
        return

    legs: list[RecentBatchLeg] = []
    for leg in ordered:
        built_leg = _build_recent_batch_leg(leg)
        if built_leg is not None:
            legs.append(built_leg)
    if not legs:
        return

    payload: RecentBatchReference = {
        "async_group_id": group_id,
        "stored_at_ts": int(time.time()),
        "legs": legs,
    }
    await redis_client.set(_recent_batch_key(identity), json.dumps(payload), ex=ASYNC_GROUP_TTL_SECONDS)


async def get_recent_batch_reference(
    redis_client: AsyncGroupRedis | None,
    *,
    identity: str | None,
) -> RecentBatchReference | None:
    if redis_client is None or not isinstance(identity, str) or not identity.strip():
        return None
    decoded = _decode_json_mapping(await redis_client.get(_recent_batch_key(identity.strip())))
    if decoded is None:
        return None

    raw_legs = decoded.get("legs")
    if not isinstance(raw_legs, list):
        return None

    legs: list[RecentBatchLeg] = []
    for leg in raw_legs:
        if not isinstance(leg, dict):
            continue
        try:
            legs.append(
                {
                    "index": int(leg.get("index") or 0),
                    "transaction_id": str(leg.get("transaction_id")).strip()
                    if leg.get("transaction_id") is not None
                    else None,
                    "task_type": str(leg.get("task_type") or "").strip(),
                    "amount": _coerce_amount(leg.get("amount")),
                    "recipient_name": str(leg.get("recipient_name")).strip()
                    if leg.get("recipient_name") is not None
                    else None,
                    "recipient_resolved_name": str(leg.get("recipient_resolved_name")).strip()
                    if leg.get("recipient_resolved_name") is not None
                    else None,
                    "recipient_label": str(leg.get("recipient_label")).strip()
                    if leg.get("recipient_label") is not None
                    else None,
                    "bank_display": str(leg.get("bank_display")).strip()
                    if leg.get("bank_display") is not None
                    else None,
                    "account_display": str(leg.get("account_display")).strip()
                    if leg.get("account_display") is not None
                    else None,
                    "final_status": cast(
                        Literal["success", "processing", "failed"],
                        _normalize_final_status(str(leg.get("final_status") or "success")),
                    ),
                    "receipt_allowed": bool(leg.get("receipt_allowed")),
                }
            )
        except Exception:
            continue
    if not legs:
        return None

    return {
        "async_group_id": str(decoded.get("async_group_id") or ""),
        "stored_at_ts": int(decoded.get("stored_at_ts") or 0),
        "legs": legs,
    }


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
    await _remember_group_target(redis_client, group_id=group_id, message=message)
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
        await _store_recent_batch_reference(redis_client, group_id=group_id, ordered=ordered, message=message)
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
    await _store_recent_batch_reference(redis_client, group_id=group_id, ordered=ordered, message=message)
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


def _normalize_final_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized in {"success", "successful", "confirmed", "completed"}:
        return "success"
    if normalized in {"pending", "processing", "queued"}:
        return "processing"
    if normalized in {"failed", "error"}:
        return "failed"
    return normalized or "success"
