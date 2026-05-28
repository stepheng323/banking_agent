"""Data execution step."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.chat.src.agent.workers.data.models.types import DataContext, DataGates, DataPayload
from apps.chat.src.agent.workers.data.pipeline.base import PipelineStep
from shared.i18n.personality import PersonalityContext, render_personalized_message
from shared.i18n.renderer import render_message
from shared.queue.factory import QueuePublisherFactory
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _data_service_metadata(payload: DataPayload) -> dict[str, Any] | None:
    metadata: dict[str, Any] = {}
    for key, value in {
        "size_gb": payload.plan_size_gb,
        "validity_days": payload.plan_validity_days,
        "tags": payload.plan_tags,
        "selection_preference": payload.selection_preference,
        "usage_intent": payload.usage_intent,
        "size_preference": payload.size_preference,
        "validity_preference": payload.validity_preference,
        "catalog_cache_stale": payload.catalog_cache_stale,
        "is_self": payload.is_self,
        "beneficiary_id": payload.beneficiary_id,
        "recipient_name": payload.recipient_name,
    }.items():
        if isinstance(value, bool):
            if value:
                metadata[key] = value
            continue
        if value not in (None, "", [], {}):
            metadata[key] = value
    return metadata or None


class ExecutionStep(PipelineStep):
    """Persist and enqueue the data purchase for async execution."""

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult | None:
        del gates
        locale = context.language

        try:
            if not payload.plan_code or payload.amount is None:
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message("data.plan_selection.missing_plan", locale),
                    response=render_message("data.plan_selection.missing_plan", locale),
                    patch=payload.model_dump(exclude_none=True),
                )

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
                        target_phone_number=payload.target_phone,
                        mobile_network=payload.network,
                        biller_code=payload.biller_code,
                        biller_item_code=payload.plan_code,
                        biller_item_name=payload.plan_name,
                        service_metadata=_data_service_metadata(payload),
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
                        "biller_code": payload.biller_code,
                        "amount": payload.amount,
                        "target_phone": payload.target_phone,
                        "network": payload.network,
                        "recipient_name": payload.recipient_name,
                        "source": payload.source_account_number or "",
                        "source_account_id": payload.source_account_id,
                        "source_account_number": payload.source_account_number,
                        "source_bank_name": payload.source_bank_name,
                        "beneficiary_id": payload.beneficiary_id,
                        "is_self": payload.is_self,
                    },
                    "async_group": async_group,
                },
            )

            recipient_phone = payload.target_phone or ""
            pending_message = render_personalized_message(
                "data.completion.pending_message",
                locale,
                {
                    "plan_name": payload.plan_name or render_message("data.format.summary.plan_name_fallback", locale),
                    "amount": f"{float(payload.amount or 0):,.2f}",
                    "recipient_phone": recipient_phone,
                },
                PersonalityContext(
                    moment="pending",
                    amount=payload.amount,
                    saved_recipient=bool(payload.beneficiary_id or payload.is_self),
                ),
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
