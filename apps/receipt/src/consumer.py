"""Receipt job consumer for queue-dispatched receipt generation jobs."""

import asyncio
import base64
import random
from collections.abc import Awaitable
from typing import Any, cast

from apps.receipt.src.renderer import (
    ReceiptBrowserRuntimeClosedError,
    ReceiptRenderer,
    is_browser_runtime_closed_error,
)
from banking.messaging.delivery.service import DeliveryService
from shared.cache.redis_client import Redis
from shared.i18n.locale import LocaleManager
from shared.i18n.renderer import render_message
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 1
RETRY_JITTER_MAX_SECONDS = 0.25


class ReceiptJobConsumer:
    """Consumes receipt generation jobs from queue payloads."""

    def __init__(
        self,
        delivery_service: DeliveryService | None = None,
        redis_client: Redis | None = None,
    ) -> None:
        self.renderer = ReceiptRenderer()
        self.redis_client = redis_client
        self.delivery_service = delivery_service or DeliveryService()

    @staticmethod
    def _extract_payload(job: dict[str, Any]) -> dict[str, Any]:
        """Return the current direct receipt job payload."""
        return job

    @staticmethod
    def _extract_transfer_data(payload: dict[str, Any]) -> dict[str, Any]:
        """Extract transfer data for renderer."""
        nested = payload.get("transfer_data")
        if isinstance(nested, dict):
            return nested
        raise ValueError("receipt_job_missing_transfer_data")

    @staticmethod
    def _extract_signal_key(job: dict[str, Any], payload: dict[str, Any]) -> str | None:
        direct = job.get("signal_key")
        if isinstance(direct, str) and direct:
            return direct
        nested = payload.get("signal_key")
        if isinstance(nested, str) and nested:
            return nested
        return None

    @staticmethod
    def _extract_beneficiary_suggestion(payload: dict[str, Any]) -> str | None:
        suggestion = payload.get("beneficiary_suggestion_message")
        if isinstance(suggestion, str) and suggestion.strip():
            return suggestion
        return None

    @staticmethod
    def _parse_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _generation_notice_text(payload: dict[str, Any]) -> str:
        explicit = payload.get("generation_notice_text")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
        locale = LocaleManager.normalize(str(payload.get("language") or payload.get("locale") or "en")).value
        return render_message("query.receipt.generating", locale)

    async def _notify_generation_started(
        self,
        *,
        payload: dict[str, Any],
        outbox_phone: Any,
        reference: Any,
    ) -> None:
        if not self._parse_bool(payload.get("send_generation_notice")):
            return

        channel = payload.get("channel", "whatsapp")
        reference_text = str(reference or "").strip()
        dedupe_key = f"receipt-generating:{reference_text}" if reference_text and reference_text != "N/A" else None
        try:
            await self.delivery_service.deliver_text(
                phone_number=str(outbox_phone),
                channel=str(channel),
                text=self._generation_notice_text(payload),
                metadata={
                    "source": "receipt_consumer",
                    "receipt_status": "generation_started",
                    "transaction_reference": reference_text,
                },
                dedupe_key=dedupe_key,
            )
        except Exception as exc:
            logger.warning(
                "receipt_generation_notice_failed",
                phone_hash=log_fingerprint(str(outbox_phone)),
                reference_hash=log_fingerprint(reference_text),
                error=str(exc),
            )

    async def _signal_completion(self, signal_key: str | None) -> None:
        """Best-effort completion signal to unblock waiters."""
        if not signal_key or not self.redis_client:
            return
        try:
            push_result = self.redis_client.rpush(signal_key, "DONE")
            if not isinstance(push_result, int):
                await cast(Awaitable[int], push_result)
            await self.redis_client.expire(signal_key, 60)  # Cleanup key quickly
        except Exception as e:
            logger.warning("receipt_signal_failed", error=str(e))

    async def _process_job(self, job: dict[str, Any]) -> None:
        """Process a single receipt job with retry logic."""
        payload = self._extract_payload(job)
        signal_key: str | None = None
        phone_number = payload.get("phone_number")
        channel_identity = payload.get("channel_identity")
        outbox_phone = channel_identity or phone_number
        reference = payload.get("transaction_reference") or "N/A"

        last_error = None
        try:
            if not phone_number:
                logger.error("receipt_job_missing_phone_number", job=job)
                return

            signal_key = self._extract_signal_key(job, payload)
            try:
                transfer_data = self._extract_transfer_data(payload)
            except ValueError as e:
                last_error = str(e)
                logger.error(
                    "receipt_job_invalid_payload",
                    phone_hash=log_fingerprint(phone_number),
                    error_hash=log_fingerprint(last_error),
                )
            else:
                await self._notify_generation_started(
                    payload=payload,
                    outbox_phone=outbox_phone,
                    reference=reference,
                )
                for attempt in range(1, MAX_RETRIES + 1):
                    attempt_started = asyncio.get_running_loop().time()
                    try:
                        logger.info(
                            "receipt_generation_attempt",
                            phone_hash=log_fingerprint(phone_number),
                            attempt=attempt,
                        )

                        image_bytes = await self.renderer.render_receipt(
                            transfer_data=transfer_data,
                            transaction_reference=reference,
                            attempt=attempt,
                        )
                        attempt_ms = (asyncio.get_running_loop().time() - attempt_started) * 1000

                        image_b64 = base64.b64encode(image_bytes).decode("ascii")
                        channel = payload.get("channel", "whatsapp")
                        intents: list[dict[str, Any]] = [
                            {
                                "type": "show_receipt",
                                "task_id": reference,
                                "receipt": {
                                    "image_base64": image_b64,
                                    "mime_type": "image/png",
                                },
                                "caption": f"Transfer Receipt: {reference}",
                                "actionable_payload": {"transaction_id": reference},
                            }
                        ]
                        beneficiary_suggestion = self._extract_beneficiary_suggestion(payload)
                        if beneficiary_suggestion:
                            intents.append({"type": "say", "text": beneficiary_suggestion})

                        await self.delivery_service.deliver_intents(
                            phone_number=str(outbox_phone),
                            channel=str(channel),
                            intents=intents,
                            metadata={"source": "receipt_consumer"},
                            dedupe_key=f"receipt:{reference}",
                            strict_actionable=True,
                        )
                        logger.info(
                            "receipt_generation_attempt_succeeded",
                            phone=phone_number,
                            attempt=attempt,
                            attempt_ms=round(attempt_ms, 2),
                        )
                        return

                    except Exception as e:
                        last_error = str(e)
                        attempt_ms = (asyncio.get_running_loop().time() - attempt_started) * 1000
                        browser_closed_error = isinstance(e, ReceiptBrowserRuntimeClosedError) or (
                            is_browser_runtime_closed_error(e)
                        )
                        logger.warning(
                            "receipt_generation_failed",
                            phone=phone_number,
                            attempt=attempt,
                            error=last_error,
                            attempt_ms=round(attempt_ms, 2),
                            error_class="browser_closed" if browser_closed_error else "other",
                        )

                        if attempt < MAX_RETRIES:
                            if browser_closed_error and attempt == 1:
                                logger.info(
                                    "receipt_generation_retry_immediate",
                                    phone=phone_number,
                                    attempt=attempt,
                                    reason="browser_closed",
                                )
                                continue
                            delay = (RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))) + random.uniform(
                                0,
                                RETRY_JITTER_MAX_SECONDS,
                            )
                            logger.info(
                                "receipt_generation_retry_scheduled",
                                phone=phone_number,
                                attempt=attempt,
                                delay_seconds=round(delay, 3),
                            )
                            await asyncio.sleep(delay)

            logger.error(
                "receipt_generation_all_retries_failed",
                phone=phone_number,
                error=last_error,
            )

            try:
                channel = payload.get("channel", "whatsapp")
                await self.delivery_service.deliver_text(
                    phone_number=str(outbox_phone),
                    channel=str(channel),
                    text=(
                        "We couldn't generate your receipt image at this time. "
                        "Don't worry - your transfer was successful! "
                        f"Reference: {reference}"
                    ),
                    metadata={"source": "receipt_consumer", "reason": "generation_failed"},
                    dedupe_key=f"receipt-fallback:{reference}",
                )
            except Exception as notify_error:
                logger.error(
                    "receipt_failure_notification_error",
                    phone=phone_number,
                    error=str(notify_error),
                )
        finally:
            await self._signal_completion(signal_key)

    async def process_job(self, job: dict[str, Any]) -> None:
        """Public job entrypoint used by non-loop consumers (Lambda worker dispatch)."""
        await self._process_job(job)
