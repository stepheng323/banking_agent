"""Airtime execution step."""

from typing import Any

from apps.core.src.agent.graphs.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from apps.core.src.agent.graphs.airtime.pipeline.base import AirtimeStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.i18n import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ExecutionStep(AirtimeStep):
    """Executes the airtime purchase."""

    async def execute(
        self,
        data: AirtimePayload,
        context: AirtimeContext,
        gates: AirtimeGates,
        worker_context: Any,
    ) -> TransactionResult:
        locale = context.language

        try:
            transaction_id = None
            key = data.idempotency_key

            from shared.database.enums import TransactionStatusEnum
            from shared.repositories.unit_of_work import UnitOfWork

            async with UnitOfWork() as uow:
                try:
                    existing = await uow.transactions.get_by_idempotency_key(key)
                    if existing:
                        transaction_id = str(existing.id)
                    else:
                        tx = await uow.transactions.create(
                            idempotency_key=key,
                            transaction_type="airtime",
                            status=TransactionStatusEnum.PENDING.value,
                            user_id=getattr(worker_context, "user_id", None),
                            amount=data.amount,
                            recipient_account_number=data.recipient_phone,
                            recipient_bank_code=data.network,  # Using bank_code field for network
                            recipient_name=data.recipient_name
                            or render_message("airtime.execution.recipient_fallback", locale),
                            recipient_bank_name=data.network,
                            source_account_id=data.source_account_id,
                            source_account_number=data.source_account_number or "",
                            source_bank_name=data.source_bank_name or "",
                            narration=f"Airtime: {data.recipient_phone} ({data.network})",
                        )
                        transaction_id = str(tx.id)
                        await uow.commit()
                        logger.info("airtime_transaction_persisted", id=transaction_id, key=key)
                except Exception as e:
                    logger.error("failed_to_persist_airtime_transaction", error=str(e))
                    raise e

            queue = worker_context.queue
            if not queue._redis:
                await queue.connect()

            airtime_data = {
                "amount": data.amount,
                "phone_number": data.recipient_phone,
                "network": data.network,
                "source_account_number": data.source_account_number,
                "source_account_id": data.source_account_id,
            }

            await queue.enqueue(
                queue_name="banking:transactions",
                message={
                    "type": "execute_airtime",
                    "idempotency_key": key,
                    "transaction_id": transaction_id,
                    "phone_number": context.phone_number,
                    "channel": context.channel,
                    "airtime_data": airtime_data,
                },
            )

            return TransactionResult(
                outcome=TransactionOutcome.OK,
                receipt={
                    "status": "queued",
                    "id": key,
                    "amount": data.amount,
                    "recipient_phone": data.recipient_phone,
                    "network": data.network,
                    "date": render_message("airtime.execution.date_now", locale),
                    "message": render_message(
                        "airtime.execution.message_queued",
                        locale,
                        {
                            "amount": f"{data.amount:,.2f}",
                            "recipient_phone": data.recipient_phone,
                            "network": data.network,
                        },
                    ),
                },
                patch={"transaction_id": transaction_id} if transaction_id else {},
            )

        except Exception as e:
            logger.error("airtime_execution_error", error=str(e), exc_info=True)
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("airtime.execution.system_error", locale),
                retryable=True,
            )
