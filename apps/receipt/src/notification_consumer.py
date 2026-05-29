"""Notification delivery consumer owned by the receipt worker runtime."""

from __future__ import annotations

import asyncio
from typing import Any

from shared.cache.redis_client import RedisClient
from shared.config.settings import settings
from shared.messaging.prompt_suppression import (
    PENDING_INPUT_PROMPT_ORIGIN_MESSAGE_ID_KEY,
    PENDING_INPUT_PROMPT_THREAD_KEY,
    is_pending_input_prompt_metadata,
    latest_inbound_delivery_target_key,
)
from banking.messaging.delivery.service import DeliveryService
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)


class NotificationJobConsumer:
    """Consumes queued outbound notification jobs and delivers them through channel clients."""

    def __init__(
        self,
        delivery_service: DeliveryService | None = None,
        *,
        redis_client: Any | None = None,
        prompt_debounce_seconds: float | None = None,
    ) -> None:
        self.delivery_service = delivery_service or DeliveryService()
        self.redis_client = redis_client
        self.prompt_debounce_seconds = (
            settings.chat_pending_input_prompt_debounce_seconds
            if prompt_debounce_seconds is None
            else prompt_debounce_seconds
        )

    @staticmethod
    def _extract_payload(job: dict[str, Any]) -> dict[str, Any]:
        return job

    @staticmethod
    def _extract_intents(payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw_intents = payload.get("intents")
        if not isinstance(raw_intents, list):
            return []
        return [dict(intent) for intent in raw_intents if isinstance(intent, dict)]

    @staticmethod
    def _parse_strict_actionable(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _get_redis_client(self) -> Any:
        if self.redis_client is not None:
            return self.redis_client
        return RedisClient.get_client()

    async def _should_drop_stale_pending_input_prompt(
        self,
        *,
        channel: str,
        delivery_target: str,
        metadata: dict[str, Any],
    ) -> bool:
        if not is_pending_input_prompt_metadata(metadata):
            return False

        origin_message_id = str(metadata.get(PENDING_INPUT_PROMPT_ORIGIN_MESSAGE_ID_KEY) or "").strip()
        if not origin_message_id:
            return False

        debounce_seconds = max(0.0, float(self.prompt_debounce_seconds))
        if debounce_seconds:
            await asyncio.sleep(debounce_seconds)

        prompt_thread_key = str(metadata.get(PENDING_INPUT_PROMPT_THREAD_KEY) or "").strip()
        latest_key = prompt_thread_key or latest_inbound_delivery_target_key(channel, delivery_target)
        try:
            latest_message_id = await self._get_redis_client().get(latest_key)
        except Exception as exc:
            logger.warning(
                "notification_pending_input_prompt_staleness_check_failed",
                channel=channel,
                phone_hash=log_fingerprint(delivery_target),
                origin_message_id_hash=log_fingerprint(origin_message_id),
                error=str(exc),
            )
            return False

        latest_message_id_text = str(latest_message_id or "").strip()
        if latest_message_id_text and latest_message_id_text != origin_message_id:
            logger.info(
                "notification_pending_input_prompt_suppressed",
                channel=channel,
                phone_hash=log_fingerprint(delivery_target),
                origin_message_id_hash=log_fingerprint(origin_message_id),
                latest_message_id_hash=log_fingerprint(latest_message_id_text),
            )
            return True

        return False

    async def process_job(self, job: dict[str, Any]) -> None:
        """Deliver one queued notification job."""
        payload = self._extract_payload(job)
        phone_number = str(payload.get("phone_number") or "").strip()
        channel = str(payload.get("channel") or "whatsapp").strip() or "whatsapp"
        intents = self._extract_intents(payload)
        metadata_raw = payload.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
        dedupe_key_raw = payload.get("dedupe_key")
        dedupe_key = str(dedupe_key_raw) if dedupe_key_raw else None
        strict_actionable = self._parse_strict_actionable(payload.get("strict_actionable", False))

        if not phone_number:
            logger.error("notification_job_missing_phone_number", payload_keys=sorted(payload.keys()))
            return
        if not intents:
            logger.warning(
                "notification_job_missing_intents",
                phone_hash=log_fingerprint(phone_number),
                channel=channel,
            )
            return

        if await self._should_drop_stale_pending_input_prompt(
            channel=channel,
            delivery_target=phone_number,
            metadata=metadata,
        ):
            return

        result = await self.delivery_service.deliver_intents(
            phone_number=phone_number,
            channel=channel,
            intents=intents,
            metadata=metadata,
            dedupe_key=dedupe_key,
            strict_actionable=strict_actionable,
        )
        logger.info(
            "notification_job_delivered",
            phone_hash=log_fingerprint(phone_number),
            channel=channel,
            intent_count=len(intents),
            dedupe_key_hash=log_fingerprint(dedupe_key),
            status=result.status,
        )
