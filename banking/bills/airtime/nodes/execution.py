"""Airtime execution step."""

from typing import Any

from banking.bills.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from banking.bills.airtime.pipeline.base import AirtimeStep
from banking.presentation.i18n.personality import PersonalityContext, render_personalized_message
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from shared.money import naira_to_json
from shared.queue.factory import QueuePublisherFactory
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _airtime_service_metadata(data: AirtimePayload) -> dict[str, Any] | None:
    metadata: dict[str, Any] = {}
    for key, value in {
        "recipient_name": data.recipient_name,
        "beneficiary_id": data.beneficiary_id,
        "is_self": data.is_self,
    }.items():
        if isinstance(value, bool):
            if value:
                metadata[key] = value
            continue
        if value not in (None, "", [], {}):
            metadata[key] = value
    return metadata or None


def _airtime_recipient_target(data: AirtimePayload, locale: str) -> str:
    phone = str(data.recipient_phone or "").strip()
    if data.is_self:
        self_label = render_message("airtime.format.summary.target_self", locale)
        return f"{self_label} ({phone})" if phone else self_label
    recipient_name = str(data.recipient_name or "").strip()
    if recipient_name:
        return f"{recipient_name} ({phone})" if phone else recipient_name
    return phone


class ExecutionStep(AirtimeStep):
    """Executes the airtime purchase."""

    async def execute(
        self,
        data: AirtimePayload,
        context: AirtimeContext,
        gates: AirtimeGates,
        worker_context: Any,
    ) -> TransactionResult:
        del gates
        locale = context.language

        try:
            transaction_id = None
            key = data.idempotency_key

            from banking.persistence.unit_of_work import UnitOfWork
            from shared.database.enums import TransactionStatusEnum

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
                            target_phone_number=data.recipient_phone,
                            mobile_network=data.network,
                            service_metadata=_airtime_service_metadata(data),
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

            publisher = getattr(worker_context, "publisher", None)
            if not publisher:
                publisher = QueuePublisherFactory.get_async_publisher()

            amount_naira = naira_to_json(data.amount) or "0.00"
            airtime_data = {
                "amount": amount_naira,
                "amount_naira": amount_naira,
                "phone_number": data.recipient_phone,
                "network": data.network,
                "recipient_name": data.recipient_name,
                "source_account_number": data.source_account_number,
                "source_account_id": data.source_account_id,
                "source_bank_name": data.source_bank_name,
                "beneficiary_id": data.beneficiary_id,
                "is_self": data.is_self,
            }
            async_group = None
            if data.async_group_id and data.async_group_size and data.async_group_kind and data.async_group_index:
                async_group = {
                    "async_group_id": data.async_group_id,
                    "async_group_size": data.async_group_size,
                    "async_group_kind": data.async_group_kind,
                    "async_group_index": data.async_group_index,
                }

            await publisher.publish(
                topic="transaction.execute",
                message={
                    "type": "execute_airtime",
                    "idempotency_key": key,
                    "transaction_id": transaction_id,
                    "phone_number": context.phone_number,
                    "channel": context.channel,
                    "channel_identity": getattr(worker_context, "channel_identity", None),
                    "language": locale,
                    "airtime_data": airtime_data,
                    "async_group": async_group,
                },
            )
            recipient_target = _airtime_recipient_target(data, locale)
            logger.info(
                "airtime_job_published",
                transaction_id=transaction_id,
                channel=context.channel,
                has_channel_identity=bool(getattr(worker_context, "channel_identity", None)),
            )

            return TransactionResult(
                outcome=TransactionOutcome.OK,
                receipt={
                    "status": "processing",
                    "id": key,
                    "amount": data.amount,
                    "recipient_phone": data.recipient_phone,
                    "network": data.network,
                    "date": render_message("airtime.execution.date_now", locale),
                    "message": render_personalized_message(
                        "airtime.execution.message_queued",
                        locale,
                        {
                            "amount": f"{data.amount:,.2f}",
                            "recipient_phone": data.recipient_phone,
                            "recipient_target": recipient_target,
                            "network": data.network,
                        },
                        PersonalityContext(
                            moment="pending",
                            amount=data.amount,
                            saved_recipient=bool(data.beneficiary_id or data.is_self),
                        ),
                    ),
                },
                patch={"transaction_id": transaction_id, "final_status": "processing"} if transaction_id else {"final_status": "processing"},
            )

        except Exception as e:
            logger.error("airtime_execution_error", error=str(e), exc_info=True)
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("airtime.execution.system_error", locale),
                retryable=True,
            )
