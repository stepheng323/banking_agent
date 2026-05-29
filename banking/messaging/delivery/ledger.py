"""Dedupe ledger operations for direct delivery."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import Any, TypeVar, cast

from shared.messaging.intents import UiIntent
from banking.messaging.delivery import actionables as delivery_actionables
from banking.messaging.delivery.models import DeliveryAttemptResult, DeliveryAttemptStatus
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)
_T = TypeVar("_T")
ScheduleActionablePersist = Callable[
    [str, str, list[UiIntent], list[str]],
    None,
]


async def _await_maybe(value: _T | Awaitable[_T]) -> _T:
    if isawaitable(value):
        return await cast(Awaitable[_T], value)
    return value


def build_delivery_ledger_key(
    *,
    phone_number: str,
    channel: str,
    intents: list[UiIntent],
    dedupe_key: str | None,
) -> tuple[str | None, str]:
    payload = [intent.to_dict() for intent in intents]
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    if not dedupe_key:
        return None, payload_hash
    ledger_key = f"delivery:ledger:{channel}:{phone_number}:{dedupe_key}:{payload_hash}"
    return ledger_key, payload_hash


async def mark_delivery_pending(redis: Any, *, ledger_key: str, payload_hash: str) -> None:
    await _await_maybe(redis.hset(ledger_key, mapping={"status": "pending", "payload_hash": payload_hash}))
    await _await_maybe(redis.expire(ledger_key, 86400))


async def mark_delivery_sent(redis: Any, *, ledger_key: str, message_ids: list[str]) -> None:
    await _await_maybe(
        redis.hset(
            ledger_key,
            mapping={"status": "sent", "message_ids": json.dumps(message_ids)},
        )
    )


async def mark_delivery_unknown(redis: Any, *, ledger_key: str) -> None:
    await _await_maybe(redis.hset(ledger_key, mapping={"status": "unknown"}))


async def mark_delivery_completed(redis: Any, *, ledger_key: str) -> None:
    await _await_maybe(redis.hset(ledger_key, mapping={"status": "completed"}))


async def resume_delivery_if_already_sent(
    redis: Any,
    *,
    ledger_key: str,
    phone_number: str,
    channel: str,
    intents: list[UiIntent],
    strict_actionable: bool,
    metadata: dict[str, Any],
    schedule_actionable_persist: ScheduleActionablePersist,
) -> DeliveryAttemptResult | None:
    status = await _await_maybe(redis.hget(ledger_key, "status"))
    if status == "completed":
        logger.info("delivery_dedupe_hit", ledger_key_hash=log_fingerprint(ledger_key), status=status)
        log_progress_dedupe(status="deduped_completed", ledger_key=ledger_key, metadata=metadata)
        return DeliveryAttemptResult(status="deduped_completed")

    if status != "sent":
        return None

    message_ids_raw = await _await_maybe(redis.hget(ledger_key, "message_ids"))
    message_ids = []
    if message_ids_raw:
        try:
            parsed = json.loads(message_ids_raw)
            if isinstance(parsed, list):
                message_ids = [str(item) for item in parsed]
        except json.JSONDecodeError:
            message_ids = []

    if strict_actionable:
        await delivery_actionables.persist_actionable_if_any(
            phone_number=phone_number,
            channel=channel,
            intents=intents,
            message_ids=message_ids,
            strict_actionable=True,
        )
    else:
        schedule_actionable_persist(phone_number, channel, intents, message_ids)
    await mark_delivery_completed(redis, ledger_key=ledger_key)
    logger.info("delivery_dedupe_resume_completed", ledger_key_hash=log_fingerprint(ledger_key))
    log_progress_dedupe(status="deduped_resumed", ledger_key=ledger_key, metadata=metadata)
    return DeliveryAttemptResult(status="deduped_resumed", message_ids=tuple(message_ids))


def log_progress_dedupe(
    *,
    status: DeliveryAttemptStatus,
    ledger_key: str,
    metadata: dict[str, Any],
) -> None:
    progress_stage = metadata.get("progress_stage")
    if not isinstance(progress_stage, str) or not progress_stage:
        return
    logger.info(
        "delivery_progress_dedupe_hit",
        status=status,
        ledger_key_hash=log_fingerprint(ledger_key),
        progress_stage=progress_stage,
        turn_id_hash=log_fingerprint(metadata.get("progress_turn_id")),
        dedupe_key_hash=log_fingerprint(metadata.get("dedupe_key")),
    )


__all__ = [
    "build_delivery_ledger_key",
    "mark_delivery_completed",
    "mark_delivery_pending",
    "mark_delivery_sent",
    "mark_delivery_unknown",
    "resume_delivery_if_already_sent",
]
