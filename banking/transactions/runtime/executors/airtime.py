"""Airtime Executor.

Handles execution of airtime transactions from the queue.
"""

from __future__ import annotations

from typing import Any

import banking.transactions.runtime.scheduled_runs as scheduled_runs
from banking.beneficiaries.services.post_transaction_beneficiary import (
    BeneficiarySuggestionServiceProtocol,
    suggest_mobile_beneficiary,
)
from banking.messaging.delivery.service import DeliveryService
from banking.persistence.unit_of_work import UnitOfWork
from banking.policy.service import capability_block_message
from banking.presentation.i18n.personality import PersonalityContext, TransferMoment
from banking.presentation.i18n.renderer import render_message
from banking.transactions.repositories.transaction_repository import TransactionRepository
from banking.transactions.runtime.async_completion import (
    is_grouped_async_message,
    record_group_leg_and_maybe_build_summary,
)
from banking.transactions.runtime.async_group_types import AsyncGroupRedis
from banking.transactions.runtime.bill_completion_notifications import (
    build_airtime_completion_context,
    merge_completion_context,
)
from banking.transactions.runtime.failure_categories import classify_failure_category
from banking.transactions.runtime.transaction_debit_helpers import debit_reference_for_transaction
from shared.clients.abstractions.bill import BillPaymentProvider
from shared.database.enums import TransactionStatusEnum
from shared.money import MoneyAmount, to_naira
from shared.queue.adapter import QueuePublisher
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _execution_error_message(locale: str) -> str:
    return render_message("airtime.error.execution_failed", locale)


def _airtime_personality_context(
    airtime_data: dict[str, Any],
    *,
    amount: MoneyAmount | int | str | None,
    moment: TransferMoment,
) -> PersonalityContext:
    return PersonalityContext(
        moment=moment,
        amount=to_naira(amount),
        saved_recipient=bool(airtime_data.get("beneficiary_id") or airtime_data.get("is_self")),
    )


def _airtime_recipient_target(airtime_data: dict[str, Any], recipient_phone: Any) -> str:
    phone = str(recipient_phone or "").strip()
    if airtime_data.get("is_self"):
        return f"My Number ({phone})" if phone else "My Number"
    recipient_name = str(airtime_data.get("recipient_name") or airtime_data.get("name") or "").strip()
    if recipient_name:
        return f"{recipient_name} ({phone})" if phone else recipient_name
    return phone


class AirtimeExecutor:
    """Executor for Airtime transactions."""

    def __init__(
        self,
        bill_provider: BillPaymentProvider,
        transaction_repo: TransactionRepository,
        publisher: QueuePublisher,
        account_provider_name: str = "mono",
        delivery_service: DeliveryService | None = None,
        redis_client: AsyncGroupRedis | None = None,
        beneficiary_suggestion_service: BeneficiarySuggestionServiceProtocol | None = None,
    ):
        self.bill_provider = bill_provider
        self.transaction_repo = transaction_repo
        self.publisher = publisher
        self.account_provider_name = account_provider_name
        self.delivery_service = delivery_service or DeliveryService()
        self.redis_client = redis_client
        self.beneficiary_suggestion_service = beneficiary_suggestion_service

    async def _build_airtime_beneficiary_suggestion(
        self,
        *,
        data: dict[str, Any],
        airtime_data: dict[str, Any],
        transaction_id: str,
        locale: str,
    ) -> str | None:
        if is_grouped_async_message(data):
            return None
        if scheduled_runs.is_scheduled_run(scheduled_runs.scheduled_meta(data)):
            return None

        return await suggest_mobile_beneficiary(
            self.beneficiary_suggestion_service,
            phone_number=str(data.get("phone_number") or ""),
            channel=str(data.get("channel") or "whatsapp"),
            locale=locale,
            transaction_id=transaction_id,
            beneficiary_type="airtime",
            recipient_phone=airtime_data.get("phone_number"),
            network=airtime_data.get("network"),
            recipient_name=airtime_data.get("recipient_name") or airtime_data.get("name"),
        )

    async def handle_airtime(self, data: dict[str, Any]) -> None:
        """Handle execution of an airtime transaction."""
        transaction_id = data.get("transaction_id")
        airtime_data = data.get("airtime_data", {})
        locale = data.get("language", "en")
        scheduled_meta = scheduled_runs.scheduled_meta(data)
        schedule_run_id = scheduled_runs.schedule_run_id(scheduled_meta)
        is_scheduled = scheduled_runs.is_scheduled_run(scheduled_meta)

        if not transaction_id:
            logger.error("airtime_execution_error", error="missing_transaction_id")
            return

        logger.info("executing_airtime", transaction_id=transaction_id)

        try:
            if is_scheduled:
                policy_block_message = capability_block_message(
                    domain="schedule",
                    action="schedule_airtime",
                    locale=locale,
                ) or capability_block_message(domain="airtime", action="buy_airtime", locale=locale)
                if policy_block_message:
                    logger.info("scheduled_airtime_execution_policy_blocked", transaction_id=transaction_id)
                    await scheduled_runs.update_scheduled_run(
                        schedule_run_id,
                        status="failed",
                        error_message=policy_block_message,
                        transaction_id=transaction_id,
                        warning_event="scheduled_airtime_run_update_failed",
                    )
                    await self.transaction_repo.update_status(
                        transaction_id,
                        TransactionStatusEnum.FAILED.value,
                        error_message=policy_block_message,
                    )
                    return
                await scheduled_runs.update_scheduled_run(
                    schedule_run_id,
                    status="processing",
                    transaction_id=transaction_id,
                    warning_event="scheduled_airtime_run_update_failed",
                )
            await self.transaction_repo.update_status(transaction_id, TransactionStatusEnum.PROCESSING.value)

            amount = to_naira(airtime_data.get("amount"))
            if amount is None or amount <= 0:
                raise ValueError("invalid_airtime_amount")
            channel = data.get("channel", "whatsapp")
            logger.info(
                "airtime_delivery_target_selected",
                transaction_id=transaction_id,
                channel=channel,
                has_channel_identity=bool(data.get("channel_identity")),
                used_fallback_phone=bool(data.get("phone_number")) and not bool(data.get("channel_identity")),
            )
            request_reference = str(data.get("idempotency_key") or transaction_id)

            await self._queue_transaction_debit(
                transaction_id=str(transaction_id),
                idempotency_key=request_reference,
                source_account_id=airtime_data.get("source_account_id"),
                completion_context=build_airtime_completion_context(
                    message=data,
                    airtime_data=airtime_data,
                    amount=amount,
                ),
            )
            logger.info("airtime_debit_queued", transaction_id=transaction_id)
            return

        except Exception as e:
            logger.error("airtime_execution_exception", transaction_id=transaction_id, error=str(e))
            error_msg = _execution_error_message(locale)
            await self.transaction_repo.update_status(
                transaction_id, TransactionStatusEnum.FAILED.value, error_message=error_msg
            )
            await scheduled_runs.update_scheduled_run(
                schedule_run_id,
                status="failed",
                error_message=error_msg,
                transaction_id=transaction_id,
                warning_event="scheduled_airtime_run_update_failed",
            )
            completion_payload = {
                "amount": airtime_data.get("amount"),
                "recipient_phone": airtime_data.get("phone_number"),
                "network": airtime_data.get("network"),
                "source_account_id": airtime_data.get("source_account_id"),
                "source_account_number": airtime_data.get("source_account_number"),
                "source_bank_name": airtime_data.get("source_bank_name"),
                "source_affinity_mode": airtime_data.get("source_affinity_mode"),
                "final_status": "failed",
                "error_message": error_msg,
                "failure_category": classify_failure_category(message=str(e), context="execution"),
            }
            batch_summary = await record_group_leg_and_maybe_build_summary(
                self.redis_client,
                message=data,
                task_type="airtime",
                payload=completion_payload,
                locale=locale,
            )
            if batch_summary:
                delivery_target = str(data.get("channel_identity") or data.get("phone_number") or "").strip()
                if delivery_target:
                    await self.delivery_service.deliver_text(
                        phone_number=delivery_target,
                        channel=data.get("channel", "whatsapp"),
                        text=batch_summary["text"],
                        actionable_payload=batch_summary.get("actionable_payload"),
                        metadata={
                            "source": "airtime_executor",
                            "transaction_id": transaction_id,
                            "batched": True,
                            "summary_stage": batch_summary["stage"],
                        },
                        dedupe_key=f"airtime:batch:{batch_summary['stage']}:{transaction_id}",
                    )

    async def _queue_transaction_debit(
        self,
        *,
        transaction_id: str,
        idempotency_key: str,
        source_account_id: str | None,
        completion_context: dict[str, Any],
    ) -> None:
        """Create/reuse transaction debit state and queue Mono debit processing."""
        if not source_account_id:
            await self.transaction_repo.update_status(
                transaction_id,
                TransactionStatusEnum.FAILED.value,
                error_message="Source account missing for airtime purchase",
            )
            return

        async with UnitOfWork() as uow:
            if not uow.transactions or not uow.transaction_debit_steps:
                return
            tx = await uow.transactions.get_by_id(transaction_id)
            if not tx:
                return
            tx.service_metadata = merge_completion_context(
                getattr(tx, "service_metadata", None),
                completion_context,
            )
            if getattr(uow, "db", None):
                uow.db.add(tx)
            await uow.transaction_debit_steps.get_or_create_for_transaction(
                transaction=tx,
                account_id=str(source_account_id),
                provider_reference=debit_reference_for_transaction(tx),
                provider_name=self.account_provider_name,
            )
            await uow.commit()

        await self.publisher.publish(
            topic="transaction_debit.process",
            message={
                "transaction_id": transaction_id,
                "idempotency_key": idempotency_key,
            },
        )
