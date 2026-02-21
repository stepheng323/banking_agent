"""Airtime Executor.

Handles execution of airtime transactions from the queue.
"""

from typing import Any

from shared.clients.abstractions.bill import BillPaymentProvider
from shared.database.enums import TransactionStatusEnum
from shared.i18n import render_message
from shared.queue.messages import OUTBOX_QUEUE
from shared.queue.redis_queue import RedisQueue
from shared.repositories.transaction_repository import TransactionRepository
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class AirtimeExecutor:
    """Executor for Airtime transactions."""

    def __init__(
        self,
        bill_provider: BillPaymentProvider,
        transaction_repo: TransactionRepository,
        queue: RedisQueue,
    ):
        self.bill_provider = bill_provider
        self.transaction_repo = transaction_repo
        self.queue = queue

    async def handle_airtime(self, data: dict[str, Any]) -> None:
        """Handle execution of an airtime transaction."""
        transaction_id = data.get("transaction_id")
        airtime_data = data.get("airtime_data", {})
        locale = data.get("language", "en")

        if not transaction_id:
            logger.error("airtime_execution_error", error="missing_transaction_id")
            return

        logger.info("executing_airtime", transaction_id=transaction_id)

        try:
            await self.transaction_repo.update_status(transaction_id, TransactionStatusEnum.PROCESSING.value)

            amount = airtime_data.get("amount")
            phone_number = airtime_data.get("phone_number")
            network = airtime_data.get("network")

            result = await self.bill_provider.purchase_airtime(
                amount=amount,
                recipient_phone=phone_number,
                network=network,
            )

            if result.get("success"):
                await self.transaction_repo.update_status(transaction_id, TransactionStatusEnum.SUCCESSFUL.value)
                logger.info("airtime_success", transaction_id=transaction_id, ref=result.get("reference"))

                if phone_number:
                    ref = result.get("reference") or render_message("airtime.executor.reference_fallback", locale)
                    message = render_message(
                        "airtime.executor.success_message",
                        locale,
                        {"amount": f"{amount:,.2f}", "reference": ref},
                    )
                    await self.queue.enqueue(
                        queue_name=OUTBOX_QUEUE,
                        message={
                            "phone_number": phone_number,
                            "channel": data.get("channel", "whatsapp"),
                            "intents": [{"type": "say", "text": message}],
                            "metadata": {
                                "source": "airtime_executor",
                                "transaction_id": transaction_id
                            },
                        }
                    )
            else:
                error_msg = result.get("message") or render_message("airtime.error.provider_failed", locale)
                await self.transaction_repo.update_status(
                    transaction_id, TransactionStatusEnum.FAILED.value, error_message=error_msg
                )
                logger.error("airtime_failed", transaction_id=transaction_id, error=error_msg)

        except Exception as e:
            logger.error("airtime_execution_exception", transaction_id=transaction_id, error=str(e))
            await self.transaction_repo.update_status(
                transaction_id, TransactionStatusEnum.FAILED.value, error_message=str(e)
            )
