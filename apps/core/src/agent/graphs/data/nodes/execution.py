"""Data execution step."""

from typing import Any

from apps.core.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.core.src.agent.graphs.data.pipeline.base import PipelineStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.i18n import render_message
from shared.queue.factory import QueuePublisherFactory
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ExecutionStep(PipelineStep):
    """Persist and enqueue the data purchase for async execution."""

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult | None:
        del gates
        locale = context.language

        try:
            transaction_id = None
            key = payload.idempotency_key

            from shared.database.enums import TransactionStatusEnum
            from shared.repositories.unit_of_work import UnitOfWork

            async with UnitOfWork() as uow:
                existing = await uow.transactions.get_by_idempotency_key(str(key))
                if existing:
                    transaction_id = str(existing.id)
                else:
                    tx = await uow.transactions.create(
                        idempotency_key=str(key),
                        transaction_type="data",
                        status=TransactionStatusEnum.PENDING.value,
                        user_id=getattr(worker_context, "user_id", None),
                        amount=payload.amount,
                        recipient_account_number=payload.target_phone or "",
                        recipient_bank_code=payload.network or "",
                        recipient_name=payload.plan_name or render_message("data.format.summary.plan_name_fallback", locale),
                        recipient_bank_name=payload.network or "",
                        source_account_id=payload.source_account_id,
                        source_account_number=payload.source_account_number or "",
                        source_bank_name=payload.source_bank_name or "",
                        narration=(
                            f"Data: {payload.plan_name} for {payload.target_phone}"
                            if payload.plan_name
                            else f"Data for {payload.target_phone}"
                        ),
                    )
                    transaction_id = str(tx.id)
                    await uow.commit()
                    logger.info("data_transaction_persisted", id=transaction_id, key=key)

            publisher = getattr(worker_context, "publisher", None)
            if not publisher:
                publisher = QueuePublisherFactory.get_async_publisher()

            async_group = None
            if (
                payload.async_group_id
                and payload.async_group_size
                and payload.async_group_kind
                and payload.async_group_index
            ):
                async_group = {
                    "async_group_id": payload.async_group_id,
                    "async_group_size": payload.async_group_size,
                    "async_group_kind": payload.async_group_kind,
                    "async_group_index": payload.async_group_index,
                }

            await publisher.publish(
                topic="transaction.execute",
                message={
                    "type": "execute_data",
                    "idempotency_key": str(key),
                    "transaction_id": transaction_id,
                    "phone_number": context.phone_number,
                    "channel": context.channel,
                    "channel_identity": getattr(worker_context, "channel_identity", None),
                    "language": locale,
                    "data_purchase": {
                        "plan_code": payload.plan_code,
                        "plan_name": payload.plan_name,
                        "amount": payload.amount,
                        "target_phone": payload.target_phone,
                        "network": payload.network,
                        "source": payload.source_account_number or "",
                    },
                    "async_group": async_group,
                },
            )

            recipient_phone = payload.target_phone or ""
            pending_message = render_message(
                "data.completion.pending_message",
                locale,
                {
                    "plan_name": payload.plan_name or render_message("data.format.summary.plan_name_fallback", locale),
                    "amount": f"{float(payload.amount or 0):,.2f}",
                    "recipient_phone": recipient_phone,
                },
            )
            receipt = {
                "id": str(key),
                "amount": payload.amount,
                "status": "processing",
                "message": pending_message,
            }

            if payload.plan_name:
                receipt["description"] = render_message(
                    "data.execution.receipt_description_plan",
                    locale,
                    {"plan_name": payload.plan_name, "target_phone": recipient_phone},
                )
            else:
                receipt["description"] = render_message(
                    "data.execution.receipt_description_network",
                    locale,
                    {"network": payload.network or "", "target_phone": recipient_phone},
                )

            return TransactionResult(
                outcome=TransactionOutcome.OK,
                receipt=receipt,
                patch={"transaction_id": transaction_id, "receipt": receipt},
            )

        except Exception as exc:
            logger.error("data_execution_error", error=str(exc), exc_info=True)
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("data.error.system_processing", locale),
                retryable=True,
            )
