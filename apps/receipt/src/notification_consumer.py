"""Notification delivery consumer owned by the receipt worker runtime."""

from __future__ import annotations

from typing import Any

from shared.services.delivery_service import DeliveryService
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)


class NotificationJobConsumer:
    """Consumes queued outbound notification jobs and delivers them through channel clients."""

    def __init__(self, delivery_service: DeliveryService | None = None) -> None:
        self.delivery_service = delivery_service or DeliveryService()

    @staticmethod
    def _extract_payload(job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload")
        if isinstance(payload, dict):
            return payload
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
