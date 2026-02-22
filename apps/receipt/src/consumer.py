"""Redis queue consumer for receipt generation jobs."""

import asyncio
import base64
from collections.abc import Awaitable
from typing import Any, cast

from apps.receipt.src.renderer import ReceiptRenderer
from shared.queue.messages import OUTBOX_QUEUE
from shared.queue.redis_queue import RedisQueue
from shared.utils.logging import get_logger

logger = get_logger(__name__)

QUEUE_NAME = "banking:receipt_jobs"
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2


class ReceiptJobConsumer:
    """Consumes receipt generation jobs from Redis queue."""

    def __init__(self) -> None:
        self.running = False
        self.renderer = ReceiptRenderer()
        self.queue = RedisQueue()

    @staticmethod
    def _extract_payload(job: dict[str, Any]) -> dict[str, Any]:
        """Support both legacy wrapped payload and direct payload job formats."""
        payload = job.get("payload")
        if isinstance(payload, dict):
            return payload
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

    async def _signal_completion(self, signal_key: str | None) -> None:
        """Best-effort completion signal to unblock waiters."""
        if not signal_key or not self.queue._redis:
            return
        try:
            push_result = self.queue._redis.rpush(signal_key, "DONE")
            if not isinstance(push_result, int):
                await cast(Awaitable[int], push_result)
            await self.queue._redis.expire(signal_key, 60)  # Cleanup key quickly
        except Exception as e:
            logger.warning("receipt_signal_failed", error=str(e))

    async def start(self) -> None:
        """Start consuming jobs from the queue."""
        self.running = True
        while self.running:
            try:
                job = await self.queue.dequeue_blocking(QUEUE_NAME, timeout=5)

                if job is None:
                    continue

                payload = self._extract_payload(job)
                logger.info(
                    "receipt_job_received",
                    phone=payload.get("phone_number"),
                    has_payload=bool(payload),
                    job=job,
                )

                await self._process_job(job)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("receipt_consumer_error", error=str(e))
                await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the consumer gracefully."""
        self.running = False
        await self.renderer.close()
        logger.info("receipt_consumer_stopped")

    async def _process_job(self, job: dict[str, Any]) -> None:
        """Process a single receipt job with retry logic."""
        payload = self._extract_payload(job)
        signal_key = self._extract_signal_key(job, payload)
        phone_number = payload.get("phone_number")
        reference = payload.get("transaction_reference") or "N/A"

        last_error = None
        try:
            if not phone_number:
                logger.error("receipt_job_missing_phone_number", job=job)
                return

            try:
                transfer_data = self._extract_transfer_data(payload)
            except ValueError as e:
                last_error = str(e)
                logger.error("receipt_job_invalid_payload", phone=phone_number, error=last_error)
            else:
                for attempt in range(1, MAX_RETRIES + 1):
                    try:
                        logger.info(
                            "receipt_generation_attempt",
                            phone=phone_number,
                            attempt=attempt,
                        )

                        image_bytes = await self.renderer.render_receipt(
                            transfer_data=transfer_data,
                            transaction_reference=reference,
                        )

                        image_b64 = base64.b64encode(image_bytes).decode("ascii")
                        intents: list[dict[str, Any]] = [
                            {
                                "type": "show_receipt",
                                "task_id": reference,
                                "receipt": {
                                    "image_base64": image_b64,
                                    "mime_type": "image/png",
                                },
                                "caption": f"Transfer Receipt: {reference}",
                            }
                        ]
                        beneficiary_suggestion = self._extract_beneficiary_suggestion(payload)
                        if beneficiary_suggestion:
                            intents.append({"type": "say", "text": beneficiary_suggestion})

                        await cast(Any, self.queue).enqueue(
                            queue_name=OUTBOX_QUEUE,
                            message={
                                "phone_number": phone_number,
                                "channel": "whatsapp",
                                "intents": intents,
                                "metadata": {"source": "receipt_consumer"},
                            },
                        )
                        return

                    except Exception as e:
                        last_error = str(e)
                        logger.warning(
                            "receipt_generation_failed",
                            phone=phone_number,
                            attempt=attempt,
                            error=last_error,
                        )

                        if attempt < MAX_RETRIES:
                            await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)

            logger.error(
                "receipt_generation_all_retries_failed",
                phone=phone_number,
                error=last_error,
            )

            try:
                await cast(Any, self.queue).enqueue(
                    queue_name=OUTBOX_QUEUE,
                    message={
                        "phone_number": phone_number,
                        "channel": "whatsapp",
                        "intents": [
                            {
                                "type": "say",
                                "text": (
                                    "We couldn't generate your receipt image at this time. "
                                    "Don't worry - your transfer was successful! "
                                    f"Reference: {reference}"
                                ),
                            }
                        ],
                        "metadata": {"source": "receipt_consumer", "reason": "generation_failed"},
                    },
                )
            except Exception as notify_error:
                logger.error(
                    "receipt_failure_notification_error",
                    phone=phone_number,
                    error=str(notify_error),
                )
        finally:
            await self._signal_completion(signal_key)
