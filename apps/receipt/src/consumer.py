"""Redis queue consumer for receipt generation jobs."""

import asyncio
import base64
from typing import Any

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

    async def start(self) -> None:
        """Start consuming jobs from the queue."""
        self.running = True
        while self.running:
            try:
                job = await self.queue.dequeue_blocking(QUEUE_NAME, timeout=5)

                if job is None:
                    continue

                payload = job
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
        payload = job
        phone_number = payload.get("phone_number")
        outbox_phone = payload.get("channel_identity") or phone_number
        reference = payload.get("transaction_reference", "N/A")

        last_error = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info(
                    "receipt_generation_attempt",
                    phone=phone_number,
                    attempt=attempt,
                )

                image_bytes = await self.renderer.render_receipt(
                    transfer_data=payload.get("transfer_data", {}),
                    transaction_reference=reference,
                )

                image_b64 = base64.b64encode(image_bytes).decode("ascii")
                channel = payload.get("channel", "whatsapp")
                await self.queue.enqueue(
                    queue_name=OUTBOX_QUEUE,
                    message={
                        "phone_number": outbox_phone,
                        "channel": channel,
                        "intents": [
                            {
                                "type": "show_receipt",
                                "task_id": reference,
                                "receipt": {
                                    "image_base64": image_b64,
                                    "mime_type": "image/png",
                                },
                                "caption": f"Transfer Receipt: {reference}",
                            }
                        ],
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
            channel = payload.get("channel", "whatsapp")
            await self.queue.enqueue(
                queue_name=OUTBOX_QUEUE,
                message={
                    "phone_number": outbox_phone,
                    "channel": channel,
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
            # Signal completion to core service
            signal_key = job.get("signal_key")
            if signal_key:
                try:
                    await self.queue._redis.rpush(signal_key, "DONE")
                    await self.queue._redis.expire(signal_key, 60)  # Cleanup key quickly
                except Exception as e:
                    logger.warning("receipt_signal_failed", error=str(e))
