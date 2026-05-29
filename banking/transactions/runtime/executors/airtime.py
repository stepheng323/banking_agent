"""Airtime Executor.

Handles execution of airtime transactions from the queue.
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as redis

import banking.transactions.runtime.provider_results as provider_results
import banking.transactions.runtime.scheduled_runs as scheduled_runs
from shared.clients.abstractions.bill import BillPaymentProvider
from shared.database.enums import TransactionStatusEnum
from shared.i18n.personality import PersonalityContext, TransferMoment, render_personalized_message
from shared.i18n.renderer import render_message
from shared.policy.service import capability_block_message
from shared.queue.adapter import QueuePublisher
from banking.transactions.repositories.transaction_repository import TransactionRepository
from banking.transactions.runtime.async_completion import (
    is_grouped_async_message,
    record_group_leg_and_maybe_build_summary,
)
from banking.messaging.delivery.service import DeliveryService
from banking.transactions.runtime.failure_categories import classify_failure_category
from banking.beneficiaries.services.post_transaction_beneficiary import (
    BeneficiarySuggestionServiceProtocol,
    append_beneficiary_suggestion,
    suggest_mobile_beneficiary,
)
from shared.utils.logging import get_logger
from shared.utils.network_utils import format_network_display_name

logger = get_logger(__name__)


def _execution_error_message(locale: str) -> str:
    return render_message("airtime.error.execution_failed", locale)


def _airtime_personality_context(
    airtime_data: dict[str, Any],
    *,
    amount: float | int | None,
    moment: TransferMoment,
) -> PersonalityContext:
    return PersonalityContext(
        moment=moment,
        amount=float(amount) if amount is not None else None,
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
        delivery_service: DeliveryService | None = None,
        redis_client: redis.Redis | None = None,
        beneficiary_suggestion_service: BeneficiarySuggestionServiceProtocol | None = None,
    ):
        self.bill_provider = bill_provider
        self.transaction_repo = transaction_repo
        self.publisher = publisher
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

            amount = airtime_data.get("amount")
            recipient_phone = airtime_data.get("phone_number")
            network = airtime_data.get("network")
            network_display = format_network_display_name(network)
            recipient_target = _airtime_recipient_target(airtime_data, recipient_phone)
            delivery_target = str(data.get("channel_identity") or data.get("phone_number") or "").strip()
            channel = data.get("channel", "whatsapp")
            logger.info(
                "airtime_delivery_target_selected",
                transaction_id=transaction_id,
                channel=channel,
                has_channel_identity=bool(data.get("channel_identity")),
                used_fallback_phone=bool(data.get("phone_number")) and not bool(data.get("channel_identity")),
            )
            request_reference = str(data.get("idempotency_key") or transaction_id)

            result = await self.bill_provider.purchase_airtime(
                amount=amount,
                recipient_phone=recipient_phone,
                network=network,
                reference=request_reference,
            )
            provider_reference = provider_results.provider_reference(result) or request_reference
            provider_status = provider_results.provider_status(result) or None

            if result.get("success"):
                await self.transaction_repo.update_status(
                    transaction_id,
                    TransactionStatusEnum.SUCCESSFUL.value,
                    provider_transaction_id=provider_reference,
                    provider_status=provider_status,
                    provider_response=result,
                )
                logger.info("airtime_success", transaction_id=transaction_id, ref=provider_reference)
                await scheduled_runs.update_scheduled_run(
                    schedule_run_id,
                    status="successful",
                    transaction_id=transaction_id,
                    warning_event="scheduled_airtime_run_update_failed",
                )
                completion_payload = {
                    "amount": amount,
                    "recipient_phone": recipient_phone,
                    "network": network,
                    "source_account_id": airtime_data.get("source_account_id"),
                    "source_account_number": airtime_data.get("source_account_number"),
                    "source_bank_name": airtime_data.get("source_bank_name"),
                    "source_affinity_mode": airtime_data.get("source_affinity_mode"),
                    "final_status": "success",
                }
                batch_summary = await record_group_leg_and_maybe_build_summary(
                    self.redis_client,
                    message=data,
                    task_type="airtime",
                    payload=completion_payload,
                    locale=locale,
                )
                if batch_summary and delivery_target:
                    await self.delivery_service.deliver_text(
                        phone_number=delivery_target,
                        channel=channel,
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
                elif delivery_target and not is_grouped_async_message(data):
                    ref = provider_reference or render_message("airtime.executor.reference_fallback", locale)
                    message = render_personalized_message(
                        "airtime.executor.success_message",
                        locale,
                        {
                            "amount": f"{amount:,.2f}",
                            "recipient_phone": recipient_phone or "",
                            "recipient_target": recipient_target,
                            "network": network_display,
                            "reference": ref,
                        },
                        _airtime_personality_context(airtime_data, amount=amount, moment="success"),
                    )
                    message = append_beneficiary_suggestion(
                        message,
                        await self._build_airtime_beneficiary_suggestion(
                            data=data,
                            airtime_data=airtime_data,
                            transaction_id=transaction_id,
                            locale=locale,
                        ),
                    )
                    await self.delivery_service.deliver_text(
                        phone_number=delivery_target,
                        channel=channel,
                        text=message,
                        metadata={"source": "airtime_executor", "transaction_id": transaction_id},
                        dedupe_key=f"airtime:success:{transaction_id}",
                    )
                else:
                    logger.warning("airtime_delivery_target_missing", transaction_id=transaction_id, channel=channel)
            elif provider_results.provider_status_is_processing(result):
                await self.transaction_repo.update_status(
                    transaction_id,
                    TransactionStatusEnum.PROCESSING.value,
                    provider_transaction_id=provider_reference,
                    provider_status=provider_status,
                    provider_response=result,
                )
                logger.info(
                    "airtime_processing",
                    transaction_id=transaction_id,
                    status=provider_results.provider_status(result),
                )
                await scheduled_runs.update_scheduled_run(
                    schedule_run_id,
                    status="processing",
                    transaction_id=transaction_id,
                    warning_event="scheduled_airtime_run_update_failed",
                )
                completion_payload = {
                    "amount": amount,
                    "recipient_phone": recipient_phone,
                    "network": network,
                    "source_account_id": airtime_data.get("source_account_id"),
                    "source_account_number": airtime_data.get("source_account_number"),
                    "source_bank_name": airtime_data.get("source_bank_name"),
                    "source_affinity_mode": airtime_data.get("source_affinity_mode"),
                    "final_status": "processing",
                }
                batch_summary = await record_group_leg_and_maybe_build_summary(
                    self.redis_client,
                    message=data,
                    task_type="airtime",
                    payload=completion_payload,
                    locale=locale,
                )
                if batch_summary and delivery_target:
                    await self.delivery_service.deliver_text(
                        phone_number=delivery_target,
                        channel=channel,
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
                elif delivery_target and is_scheduled and not is_grouped_async_message(data):
                    amount_text = f"{amount:,.2f}" if amount is not None else "0.00"
                    message = render_personalized_message(
                        "airtime.execution.message_queued",
                        locale,
                        {
                            "amount": amount_text,
                            "recipient_phone": recipient_phone or "",
                            "recipient_target": recipient_target,
                            "network": network_display,
                        },
                        _airtime_personality_context(airtime_data, amount=amount, moment="pending"),
                    )
                    message = append_beneficiary_suggestion(
                        message,
                        await self._build_airtime_beneficiary_suggestion(
                            data=data,
                            airtime_data=airtime_data,
                            transaction_id=transaction_id,
                            locale=locale,
                        ),
                    )
                    await self.delivery_service.deliver_text(
                        phone_number=delivery_target,
                        channel=channel,
                        text=message,
                        metadata={"source": "airtime_executor", "transaction_id": transaction_id},
                        dedupe_key=f"airtime:processing:{transaction_id}",
                    )
                elif delivery_target and not is_grouped_async_message(data):
                    suggestion = await self._build_airtime_beneficiary_suggestion(
                        data=data,
                        airtime_data=airtime_data,
                        transaction_id=transaction_id,
                        locale=locale,
                    )
                    if suggestion:
                        message = append_beneficiary_suggestion(
                            render_message("airtime.execution.processing_status", locale),
                            suggestion,
                        )
                        await self.delivery_service.deliver_text(
                            phone_number=delivery_target,
                            channel=channel,
                            text=message,
                            metadata={"source": "airtime_executor", "transaction_id": transaction_id},
                            dedupe_key=f"airtime:processing-suggestion:{transaction_id}",
                        )
                else:
                    logger.warning("airtime_delivery_target_missing", transaction_id=transaction_id, channel=channel)
            else:
                error_msg = provider_results.provider_error_message(
                    result,
                    render_message("airtime.error.provider_failed", locale),
                )
                await self.transaction_repo.update_status(
                    transaction_id,
                    TransactionStatusEnum.FAILED.value,
                    error_message=error_msg,
                    provider_transaction_id=provider_results.provider_reference(result),
                    provider_status=provider_status,
                    provider_response=result,
                    provider_error_code=provider_results.provider_error_code(result),
                )
                logger.error("airtime_failed", transaction_id=transaction_id, error=error_msg)
                await scheduled_runs.update_scheduled_run(
                    schedule_run_id,
                    status="failed",
                    error_message=error_msg,
                    transaction_id=transaction_id,
                    warning_event="scheduled_airtime_run_update_failed",
                )
                completion_payload = {
                    "amount": amount,
                    "recipient_phone": recipient_phone,
                    "network": network,
                    "source_account_id": airtime_data.get("source_account_id"),
                    "source_account_number": airtime_data.get("source_account_number"),
                    "source_bank_name": airtime_data.get("source_bank_name"),
                    "source_affinity_mode": airtime_data.get("source_affinity_mode"),
                    "final_status": "failed",
                    "error_message": error_msg,
                    "failure_category": classify_failure_category(
                        message=error_msg,
                        code=provider_results.provider_error_code(result),
                        context="provider",
                    ),
                }
                batch_summary = await record_group_leg_and_maybe_build_summary(
                    self.redis_client,
                    message=data,
                    task_type="airtime",
                    payload=completion_payload,
                    locale=locale,
                )
                if batch_summary and delivery_target:
                    await self.delivery_service.deliver_text(
                        phone_number=delivery_target,
                        channel=channel,
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
                elif delivery_target and not is_grouped_async_message(data):
                    message = render_personalized_message(
                        "airtime.executor.failure_message",
                        locale,
                        {
                            "amount": f"{amount:,.2f}",
                            "recipient_phone": recipient_phone or "",
                            "recipient_target": recipient_target,
                            "network": network_display,
                            "reason": error_msg,
                        },
                        _airtime_personality_context(airtime_data, amount=amount, moment="failure"),
                    )
                    await self.delivery_service.deliver_text(
                        phone_number=delivery_target,
                        channel=channel,
                        text=message,
                        metadata={"source": "airtime_executor", "transaction_id": transaction_id},
                        dedupe_key=f"airtime:failed:{transaction_id}",
                    )
                else:
                    logger.warning("airtime_delivery_target_missing", transaction_id=transaction_id, channel=channel)

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
