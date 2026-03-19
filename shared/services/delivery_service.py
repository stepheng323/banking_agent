"""Shared direct delivery service for channel sends and actionable persistence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from inspect import isawaitable
from typing import Any, Literal, TypeVar, cast

from sqlalchemy.exc import IntegrityError

from apps.core.src.agent.orchestrator.models.intents import Say, UiIntent, reconstruct_intent
from apps.core.src.messaging.presenters.base import PresentationContext
from apps.core.src.messaging.presenters.factory import PresenterFactory
from shared.cache.redis_client import RedisClient
from shared.clients.abstractions.messaging import MessagingClient
from shared.clients.disabled_messaging import DisabledMessagingClient
from shared.clients.telegram.client import TelegramClient
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config.settings import settings
from shared.database.enums import ActionableMessageTypeEnum
from shared.database.models import ActionableMessage
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.logging import get_logger

logger = get_logger(__name__)
_T = TypeVar("_T")
DeliveryAttemptStatus = Literal["delivered", "deduped_completed", "deduped_resumed", "failed"]


@dataclass(frozen=True, slots=True)
class DeliveryAttemptResult:
    """Structured direct-delivery outcome for internal callers."""

    status: DeliveryAttemptStatus
    message_ids: tuple[str, ...] = field(default_factory=tuple)
    error: str | None = None

    @property
    def delivered(self) -> bool:
        return self.status == "delivered"


async def _await_maybe(value: _T | Awaitable[_T]) -> _T:
    if isawaitable(value):
        return await cast(Awaitable[_T], value)
    return value


def _is_duplicate_channel_message_error(error: IntegrityError) -> bool:
    """Return True when integrity error corresponds to duplicate channel message id."""
    message = str(getattr(error, "orig", error)).lower()
    return ("duplicate key value" in message or "unique constraint" in message) and (
        "channel_message_id" in message or "wa_message_id" in message
    )


class DeliveryService:
    """Shared service for direct channel delivery."""

    def __init__(
        self,
        messaging_clients: dict[str, MessagingClient] | None = None,
    ) -> None:
        if messaging_clients is not None:
            self.messaging_clients = messaging_clients
        elif settings.enable_outbound_sender:
            self.messaging_clients = {
                "whatsapp": WhatsAppClient(),
                "telegram": TelegramClient(),
            }
        else:
            self.messaging_clients = {
                "whatsapp": DisabledMessagingClient("whatsapp"),
                "telegram": DisabledMessagingClient("telegram"),
            }
        self.redis = RedisClient.get_client()

    async def deliver_text(
        self,
        phone_number: str,
        channel: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
        strict_actionable: bool = False,
    ) -> DeliveryAttemptResult:
        """Deliver plain text using the same pipeline as intent delivery."""
        return await self.deliver_intents(
            phone_number=phone_number,
            channel=channel,
            intents=[Say(text=text)],
            metadata=metadata,
            dedupe_key=dedupe_key,
            strict_actionable=strict_actionable,
        )

    async def deliver_intents(
        self,
        phone_number: str,
        channel: str,
        intents: Sequence[UiIntent | dict[str, Any]],
        metadata: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
        strict_actionable: bool = False,
    ) -> DeliveryAttemptResult:
        """Deliver UI intents directly via channel clients."""
        ui_intents = self._normalize_intents(intents)
        if not ui_intents:
            return DeliveryAttemptResult(status="delivered")

        ledger_key, payload_hash = self._build_ledger_key(
            phone_number=phone_number,
            channel=channel,
            intents=ui_intents,
            dedupe_key=dedupe_key,
        )

        if ledger_key:
            resumed = await self._resume_if_already_sent(
                ledger_key=ledger_key,
                phone_number=phone_number,
                channel=channel,
                intents=ui_intents,
                strict_actionable=strict_actionable,
                metadata=metadata or {},
            )
            if resumed is not None:
                return resumed

            await _await_maybe(self.redis.hset(ledger_key, mapping={"status": "pending", "payload_hash": payload_hash}))
            await _await_maybe(self.redis.expire(ledger_key, 86400))

        client = self._get_client(channel)
        presenter = PresenterFactory.create(channel=channel, client=client)
        context = PresentationContext(
            channel=channel,
            phone_number=phone_number,
            capabilities={"flows": getattr(client, "supports_flows", True)},
            metadata=metadata or {},
        )
        result = await presenter.present(ui_intents, context)
        if not result.success:
            if ledger_key:
                await _await_maybe(self.redis.hset(ledger_key, mapping={"status": "unknown"}))
            raise RuntimeError(
                f"delivery_failed channel={channel} errors={','.join(result.errors) if result.errors else 'unknown'}"
            )

        if ledger_key:
            await _await_maybe(
                self.redis.hset(
                    ledger_key,
                    mapping={"status": "sent", "message_ids": json.dumps(result.message_ids)},
                )
            )

        await self._persist_actionable_if_any(
            phone_number=phone_number,
            channel=channel,
            intents=ui_intents,
            message_ids=result.message_ids,
            strict_actionable=strict_actionable,
        )

        if ledger_key:
            await _await_maybe(self.redis.hset(ledger_key, mapping={"status": "completed"}))
        return DeliveryAttemptResult(status="delivered", message_ids=tuple(result.message_ids))

    @staticmethod
    def _normalize_intents(intents: Sequence[UiIntent | dict[str, Any]]) -> list[UiIntent]:
        normalized: list[UiIntent] = []
        for intent in intents:
            if isinstance(intent, dict):
                parsed = reconstruct_intent(intent)
                if parsed:
                    normalized.append(parsed)
                continue
            normalized.append(intent)
        return normalized

    def _get_client(self, channel: str) -> MessagingClient:
        client = self.messaging_clients.get(channel)
        if client:
            return client
        default_client = self.messaging_clients.get("whatsapp")
        if default_client:
            return default_client
        first_client = next(iter(self.messaging_clients.values()), None)
        if not first_client:
            raise RuntimeError("No messaging clients configured")
        return first_client

    @staticmethod
    def _build_ledger_key(
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

    async def _resume_if_already_sent(
        self,
        ledger_key: str,
        phone_number: str,
        channel: str,
        intents: list[UiIntent],
        strict_actionable: bool,
        metadata: dict[str, Any],
    ) -> DeliveryAttemptResult | None:
        status = await _await_maybe(self.redis.hget(ledger_key, "status"))
        if status == "completed":
            logger.info("delivery_dedupe_hit", ledger_key=ledger_key, status=status)
            self._log_progress_dedupe(
                status="deduped_completed",
                ledger_key=ledger_key,
                metadata=metadata,
            )
            return DeliveryAttemptResult(status="deduped_completed")

        if status != "sent":
            return None

        message_ids_raw = await _await_maybe(self.redis.hget(ledger_key, "message_ids"))
        message_ids = []
        if message_ids_raw:
            try:
                parsed = json.loads(message_ids_raw)
                if isinstance(parsed, list):
                    message_ids = [str(item) for item in parsed]
            except json.JSONDecodeError:
                message_ids = []

        await self._persist_actionable_if_any(
            phone_number=phone_number,
            channel=channel,
            intents=intents,
            message_ids=message_ids,
            strict_actionable=strict_actionable,
        )
        await _await_maybe(self.redis.hset(ledger_key, mapping={"status": "completed"}))
        logger.info("delivery_dedupe_resume_completed", ledger_key=ledger_key)
        self._log_progress_dedupe(
            status="deduped_resumed",
            ledger_key=ledger_key,
            metadata=metadata,
        )
        return DeliveryAttemptResult(status="deduped_resumed", message_ids=tuple(message_ids))

    @staticmethod
    def _log_progress_dedupe(
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
            ledger_key=ledger_key,
            progress_stage=progress_stage,
            turn_id=metadata.get("progress_turn_id"),
            dedupe_key=metadata.get("dedupe_key"),
        )

    async def _persist_actionable_if_any(
        self,
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
            await self._persist_actionable_message(
                channel=channel,
                message_id=message_id,
                identity_or_phone=phone_number,
                actionable_payload=actionable_payload,
                strict=strict_actionable,
            )

    async def _persist_actionable_message(
        self,
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
                    logger.warning("actionable_persist_user_not_found", channel=channel, identity=identity_or_phone)
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

                uow.actionable_messages.db.add(
                    ActionableMessage(
                        user_id=user.id,
                        channel_message_id=message_id,
                        message_type=msg_type.value,
                        message_data=actionable_payload,
                        expires_at=datetime.utcnow() + timedelta(days=7),
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
                message_id=message_id,
                error=str(exc),
                exc_info=True,
            )
            if strict:
                raise
