"""Redis queue consumer for receipt generation jobs."""

import asyncio
import json
from typing import Any

from apps.core.src.agent.tools.beneficiary.suggestion_service import BeneficiarySuggestionService
from apps.receipt_worker.src.renderer import ReceiptRenderer
from shared.cache.redis_client import RedisClient
from shared.clients.whatsapp.client import WhatsAppClient
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
        self.whatsapp_client = WhatsAppClient()
        self.beneficiary_suggestion_service = BeneficiarySuggestionService(
            whatsapp_client=self.whatsapp_client,
        )

    async def start(self) -> None:
        """Start consuming jobs from the queue."""
        self.running = True
        redis_client = RedisClient.get_client()

        logger.info("receipt_consumer_starting", queue=QUEUE_NAME)

        while self.running:
            try:
                result = await redis_client.blpop(QUEUE_NAME, timeout=5)

                if result is None:
                    continue

                _, job_data = result
                job = json.loads(job_data)

                logger.info(
                    "receipt_job_received",
                    phone=job.get("phone_number"),
                    has_transfer_data=bool(job.get("transfer_data")),
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
        phone_number = job.get("phone_number")
        transfer_data = job.get("transfer_data", {})
        transfer_result = job.get("transfer_result", {})

        last_error = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info(
                    "receipt_generation_attempt",
                    phone=phone_number,
                    attempt=attempt,
                )

                image_bytes = await self.renderer.render_receipt(
                    transfer_data=transfer_data,
                    transfer_result=transfer_result,
                )

                await self.whatsapp_client.send_image_data(
                    to=phone_number,
                    data=image_bytes,
                    caption="Your transfer receipt",
                )

                await self._suggest_beneficiary(
                    phone_number=phone_number,
                    transfer_data=transfer_data,
                    transaction_id=job.get("transaction_id"),
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
            await self.whatsapp_client.send_text(
                to=phone_number,
                message=(
                    "We couldn't generate your receipt image at this time. "
                    "Don't worry - your transfer was successful! "
                    f"Reference: {transfer_result.get('transaction_id', 'N/A')}"
                ),
            )
        except Exception as notify_error:
            logger.error(
                "receipt_failure_notification_error",
                phone=phone_number,
                error=str(notify_error),
            )

    async def _suggest_beneficiary(
        self,
        phone_number: str,
        transfer_data: dict[str, Any],
        transaction_id: str | None,
    ) -> None:
        """Suggest saving recipient as beneficiary after receipt delivery."""
        try:
            recipient = transfer_data.get("recipient", {})
            await self.beneficiary_suggestion_service.check_and_suggest_beneficiary(
                phone_number=phone_number,
                beneficiary_type="transfer",
                recipient_data=recipient,
                transaction_id=transaction_id,
            )
        except Exception as e:
            logger.warning(
                "beneficiary_suggestion_failed",
                phone=phone_number,
                error=str(e),
            )
