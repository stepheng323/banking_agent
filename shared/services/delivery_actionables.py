"""Actionable-message persistence for direct delivery."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError

from shared.database.enums import ActionableMessageTypeEnum
from shared.database.models import ActionableMessage
from shared.messaging.intents import UiIntent
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.datetime import utc_now_naive
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)


def _is_duplicate_channel_message_error(error: IntegrityError) -> bool:
    """Return True when integrity error corresponds to duplicate channel message id."""
    message = str(getattr(error, "orig", error)).lower()
    return ("duplicate key value" in message or "unique constraint" in message) and (
        "channel_message_id" in message or "wa_message_id" in message
    )


async def persist_actionable_if_any(
    *,
    phone_number: str,
    channel: str,
    intents: list[UiIntent],
    message_ids: list[str],
    strict_actionable: bool,
) -> None:
    actionable_payload = next((intent.actionable_payload for intent in intents if intent.actionable_payload), None)
    if not actionable_payload or not message_ids:
        return

    for message_id in message_ids:
        await persist_actionable_message(
            channel=channel,
            message_id=message_id,
            identity_or_phone=phone_number,
            actionable_payload=actionable_payload,
            strict=strict_actionable,
        )


async def persist_actionable_message(
    *,
    channel: str,
    message_id: str,
    identity_or_phone: str,
    actionable_payload: dict[str, Any],
    strict: bool,
) -> None:
    try:
        async with UnitOfWork() as uow:
            if not uow.users or not uow.actionable_messages:
                raise RuntimeError("missing_repositories")

            user = await uow.users.get_by_channel_identity(channel, identity_or_phone)
            if not user:
                logger.warning(
                    "actionable_persist_user_not_found",
                    channel=channel,
                    identity_hash=log_fingerprint(identity_or_phone),
                )
                if strict:
                    raise RuntimeError("actionable_user_not_found")
                return

            existing = await uow.actionable_messages.get_by_channel_message_id_for_user(
                channel_message_id=message_id,
                user_id=str(user.id),
            )
            if existing:
                return

            msg_type = (
                ActionableMessageTypeEnum.TRANSFER_RECEIPT
                if "transaction_id" in actionable_payload
                else ActionableMessageTypeEnum.CONFIRMATION_REQUEST
            )

            ttl_days_raw = actionable_payload.get("actionable_ttl_days")
            try:
                ttl_days = int(ttl_days_raw) if ttl_days_raw is not None else 7
            except (TypeError, ValueError):
                ttl_days = 7
            ttl_days = max(1, min(ttl_days, 3650))

            uow.actionable_messages.db.add(
                ActionableMessage(
                    user_id=user.id,
                    channel_message_id=message_id,
                    message_type=msg_type.value,
                    message_data=actionable_payload,
                    expires_at=utc_now_naive() + timedelta(days=ttl_days),
                )
            )

            try:
                await uow.commit()
            except IntegrityError as exc:
                await uow.rollback()
                if _is_duplicate_channel_message_error(exc):
                    return
                raise
    except Exception as exc:
        logger.error(
            "actionable_persist_failed",
            channel=channel,
            message_id_hash=log_fingerprint(message_id),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        if strict:
            raise
