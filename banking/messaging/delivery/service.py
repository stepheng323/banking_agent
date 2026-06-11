"""Shared direct delivery service for channel sends and actionable persistence."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Any

from banking.messaging.delivery import actionables as delivery_actionables
from banking.messaging.delivery.background import schedule_actionable_persist
from banking.messaging.delivery.ledger import (
    build_delivery_ledger_key,
    mark_delivery_completed,
    mark_delivery_pending,
    mark_delivery_sent,
    mark_delivery_unknown,
    resume_delivery_if_already_sent,
)
from banking.messaging.delivery.models import DeliveryAttemptResult
from shared.cache.redis_client import RedisClient
from shared.clients.abstractions.messaging import MessagingClient
from shared.clients.disabled_messaging import DisabledMessagingClient
from shared.clients.telegram.client import TelegramClient
from shared.clients.whatsapp.client import WhatsAppClient
from shared.messaging.body_blocks import MessageDocument
from shared.messaging.intents import Say, UiIntent, reconstruct_intent
from shared.messaging.presenters.base import PresentationContext
from shared.messaging.presenters.factory import PresenterFactory
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _build_default_messaging_clients() -> dict[str, MessagingClient]:
    clients: dict[str, MessagingClient] = {}

    try:
        clients["whatsapp"] = WhatsAppClient()
    except ValueError:
        clients["whatsapp"] = DisabledMessagingClient("whatsapp")

    try:
        clients["telegram"] = TelegramClient()
    except ValueError:
        clients["telegram"] = DisabledMessagingClient("telegram")

    return clients


class DeliveryService:
    """Shared service for direct channel delivery."""

    def __init__(
        self,
        messaging_clients: dict[str, MessagingClient] | None = None,
    ) -> None:
        if messaging_clients is not None:
            self.messaging_clients = messaging_clients
        else:
            self.messaging_clients = _build_default_messaging_clients()
        self.redis = RedisClient.get_client()
        self._background_tasks: set[asyncio.Task[None]] = set()

    @staticmethod
    def _log_latency_span(
        *,
        span: str,
        duration_ms: float,
        phone_number: str,
        channel: str,
        intent_count: int,
    ) -> None:
        logger.info(
            "perf_timer_latency",
            gate=span,
            duration_ms=round(duration_ms, 2),
            phone_number=phone_number,
            channel=channel,
            intent_count=intent_count,
        )

    async def deliver_text(
        self,
        phone_number: str,
        channel: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
        strict_actionable: bool = False,
        actionable_payload: dict[str, Any] | None = None,
        body_blocks: MessageDocument | None = None,
    ) -> DeliveryAttemptResult:
        """Deliver plain text using the same pipeline as intent delivery."""
        return await self.deliver_intents(
            phone_number=phone_number,
            channel=channel,
            intents=[Say(text=text, body_blocks=body_blocks, actionable_payload=actionable_payload)],
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

        ledger_key, payload_hash = build_delivery_ledger_key(
            phone_number=phone_number,
            channel=channel,
            intents=ui_intents,
            dedupe_key=dedupe_key,
        )

        if ledger_key:
            resumed = await resume_delivery_if_already_sent(
                self.redis,
                ledger_key=ledger_key,
                phone_number=phone_number,
                channel=channel,
                intents=ui_intents,
                strict_actionable=strict_actionable,
                metadata=metadata or {},
                schedule_actionable_persist=self._schedule_actionable_persist,
            )
            if resumed is not None:
                return resumed

            await mark_delivery_pending(self.redis, ledger_key=ledger_key, payload_hash=payload_hash)

        client = self._get_client(channel)
        presenter = PresenterFactory.create(channel=channel, client=client)
        context = PresentationContext(
            channel=channel,
            phone_number=phone_number,
            capabilities={"flows": getattr(client, "supports_flows", True)},
            metadata=metadata or {},
        )
        present_start = time.perf_counter()
        result = await presenter.present(ui_intents, context)
        self._log_latency_span(
            span="delivery_presenter_present",
            duration_ms=(time.perf_counter() - present_start) * 1000,
            phone_number=phone_number,
            channel=channel,
            intent_count=len(ui_intents),
        )
        if not result.success:
            if ledger_key:
                await mark_delivery_unknown(self.redis, ledger_key=ledger_key)
            raise RuntimeError(
                f"delivery_failed channel={channel} errors={','.join(result.errors) if result.errors else 'unknown'}"
            )

        if ledger_key:
            await mark_delivery_sent(self.redis, ledger_key=ledger_key, message_ids=result.message_ids)

        actionable_start = time.perf_counter()
        if strict_actionable:
            await delivery_actionables.persist_actionable_if_any(
                phone_number=phone_number,
                channel=channel,
                intents=ui_intents,
                message_ids=result.message_ids,
                strict_actionable=True,
            )
        else:
            self._schedule_actionable_persist(
                phone_number=phone_number,
                channel=channel,
                intents=ui_intents,
                message_ids=result.message_ids,
            )
        self._log_latency_span(
            span="delivery_actionable_persist",
            duration_ms=(time.perf_counter() - actionable_start) * 1000,
            phone_number=phone_number,
            channel=channel,
            intent_count=len(ui_intents),
        )

        if ledger_key:
            await mark_delivery_completed(self.redis, ledger_key=ledger_key)
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

    def _schedule_actionable_persist(
        self,
        phone_number: str,
        channel: str,
        intents: list[UiIntent],
        message_ids: list[str],
    ) -> None:
        schedule_actionable_persist(
            background_tasks=self._background_tasks,
            phone_number=phone_number,
            channel=channel,
            intents=intents,
            message_ids=message_ids,
            log_latency_span=self._log_latency_span,
        )
