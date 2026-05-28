"""Recent async-batch reference storage for support and receipt follow-ups."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Literal, cast

from shared.services.async_group_types import (
    ASYNC_GROUP_TTL_SECONDS,
    AsyncGroupRedis,
    RecentBatchLeg,
    RecentBatchReference,
)

ASYNC_GROUP_TARGET_PREFIX = "async-group:target"
ASYNC_GROUP_RECENT_BATCH_PREFIX = "async-group:recent-batch"


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


def normalize_final_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized in {"success", "successful", "confirmed", "completed"}:
        return "success"
    if normalized in {"pending", "processing", "queued"}:
        return "processing"
    if normalized in {"failed", "error"}:
        return "failed"
    return normalized or "success"


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


def _optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def resolve_recent_batch_identity(message: dict[str, Any]) -> str | None:
    for key in ("channel_identity", "phone_number"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _group_target_key(group_id: str) -> str:
    return f"{ASYNC_GROUP_TARGET_PREFIX}:{group_id}"


def _recent_batch_key(identity: str) -> str:
    return f"{ASYNC_GROUP_RECENT_BATCH_PREFIX}:{identity}"


async def remember_group_target(redis_client: AsyncGroupRedis, *, group_id: str, message: dict[str, Any]) -> None:
    identity = resolve_recent_batch_identity(message)
    if identity is None:
        return
    payload = {
        "identity": identity,
        "channel": str(message.get("channel") or "whatsapp"),
        "channel_identity": message.get("channel_identity"),
        "phone_number": message.get("phone_number"),
    }
    await redis_client.set(_group_target_key(group_id), json.dumps(payload), ex=ASYNC_GROUP_TTL_SECONDS)


async def load_group_target(redis_client: AsyncGroupRedis, *, group_id: str) -> dict[str, Any] | None:
    return _decode_json_mapping(await redis_client.get(_group_target_key(group_id)))


def build_recent_batch_leg(leg: dict[str, Any]) -> RecentBatchLeg | None:
    task_type = str(leg.get("type") or "").strip()
    payload = leg.get("payload")
    if not task_type or not isinstance(payload, dict):
        return None

    normalized_status = normalize_final_status(str(payload.get("final_status") or "success"))
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
    return cast(
        RecentBatchLeg,
        {
            "index": int(leg.get("index") or 0),
            "transaction_id": str(leg.get("transaction_id")).strip()
            if leg.get("transaction_id") is not None
            else None,
            "task_type": task_type,
            "amount": _coerce_amount(payload.get("amount")),
            "recipient_name": recipient_name,
            "recipient_resolved_name": recipient_resolved_name,
            "recipient_label": recipient_label,
            "bank_display": bank_display,
            "account_display": account_display,
            "final_status": cast(Literal["success", "processing", "failed"], normalized_status),
            "error_message": _optional_str(payload.get("error_message")),
            "failure_category": _optional_str(payload.get("failure_category")),
            "receipt_allowed": task_type == "transfer" and normalized_status == "success",
        },
    )


async def store_recent_batch_reference(
    redis_client: AsyncGroupRedis,
    *,
    group_id: str,
    ordered: list[dict[str, Any]],
    message: dict[str, Any],
) -> None:
    identity = resolve_recent_batch_identity(message)
    if identity is None:
        stored_target = await load_group_target(redis_client, group_id=group_id)
        if stored_target is not None:
            identity = str(stored_target.get("identity") or "").strip() or None
    if identity is None:
        return

    legs: list[RecentBatchLeg] = []
    for leg in ordered:
        built_leg = build_recent_batch_leg(leg)
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
                        normalize_final_status(str(leg.get("final_status") or "success")),
                    ),
                    "error_message": str(leg.get("error_message")).strip()
                    if leg.get("error_message") is not None
                    else None,
                    "failure_category": str(leg.get("failure_category")).strip()
                    if leg.get("failure_category") is not None
                    else None,
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


__all__ = [
    "build_recent_batch_leg",
    "get_recent_batch_reference",
    "load_group_target",
    "normalize_final_status",
    "remember_group_target",
    "resolve_recent_batch_identity",
    "store_recent_batch_reference",
]
