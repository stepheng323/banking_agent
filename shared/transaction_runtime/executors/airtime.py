"""Airtime Executor.

Handles execution of airtime transactions from the queue.
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as redis

from shared.clients.abstractions.bill import BillPaymentProvider
from shared.database.enums import TransactionStatusEnum
from shared.i18n import render_message
from shared.i18n.personality import PersonalityContext, TransferMoment, render_personalized_message
from shared.policy.service import capability_block_message
from shared.queue.adapter import QueuePublisher
from shared.repositories.transaction_repository import TransactionRepository
from shared.repositories.unit_of_work import UnitOfWork
from shared.services.async_completion import (
    is_grouped_async_message,
    record_group_leg_and_maybe_build_summary,
)
from shared.services.delivery_service import DeliveryService
from shared.services.failure_categories import classify_failure_category
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _provider_error_message(result: dict[str, Any], fallback: str) -> str:
    for key in ("message", "error", "reason"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _provider_error_code(result: dict[str, Any]) -> str | None:
    for key in ("response_code", "responseCode", "error_code", "code"):
        value = result.get(key)
        if value is not None:
            return str(value)
    return None


def _provider_reference(result: dict[str, Any]) -> str | None:
    for key in ("transaction_id", "reference", "ref", "provider_reference"):
        value = result.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()

    for nested_key in ("data", "raw_response"):
        nested = result.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for key in ("transaction_id", "reference", "ref", "provider_reference"):
            value = nested.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return None


def _provider_status(result: dict[str, Any]) -> str:
    for key in ("status", "provider_status", "transaction_status", "tx_status"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()

    for nested_key in ("data", "raw_response"):
        nested = result.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for key in ("status", "provider_status", "transaction_status", "tx_status"):
            value = nested.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()

    for key in ("message", "error", "reason"):
        value = result.get(key)
        if isinstance(value, str) and value.strip().lower() in {
            "pending",
            "processing",
            "queued",
            "bill payment is pending",
        }:
            return "pending"
    return ""


def _provider_status_is_processing(result: dict[str, Any]) -> bool:
    return _provider_status(result) in {"pending", "processing", "queued"}


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


class AirtimeExecutor:
    """Executor for Airtime transactions."""

    def __init__(
        self,
        bill_provider: BillPaymentProvider,
        transaction_repo: TransactionRepository,
        publisher: QueuePublisher,
        delivery_service: DeliveryService | None = None,
        redis_client: redis.Redis | None = None,
    ):
        self.bill_provider = bill_provider
        self.transaction_repo = transaction_repo
        self.publisher = publisher
        self.delivery_service = delivery_service or DeliveryService()
        self.redis_client = redis_client

    @staticmethod
    def _scheduled_meta(data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("scheduled_meta")
        return raw if isinstance(raw, dict) else {}

    async def _update_scheduled_run(
        self,
        schedule_run_id: str | None,
        *,
        status: str,
        error_message: str | None = None,
        transaction_id: str | None = None,
    ) -> None:
        if not schedule_run_id:
            return
        try:
            async with UnitOfWork() as uow:
                if not uow.scheduled_runs:
                    return
                run = await uow.scheduled_runs.get_by_id(schedule_run_id)
                if not run:
                    return
                run.status = status
                run.error_message = error_message
                run.transaction_id = transaction_id or run.transaction_id
                if status in {"successful", "failed"}:
                    from datetime import UTC, datetime

                    run.completed_at = datetime.now(UTC).replace(tzinfo=None)
                uow.db.add(run)
                await uow.commit()
        except Exception as exc:
            logger.warning("scheduled_airtime_run_update_failed", schedule_run_id=schedule_run_id, error=str(exc))

    async def handle_airtime(self, data: dict[str, Any]) -> None:
        """Handle execution of an airtime transaction."""
        transaction_id = data.get("transaction_id")
        airtime_data = data.get("airtime_data", {})
        locale = data.get("language", "en")
        scheduled_meta = self._scheduled_meta(data)
        schedule_run_id = str(scheduled_meta.get("schedule_run_id")) if scheduled_meta.get("schedule_run_id") else None
        is_scheduled = str(scheduled_meta.get("run_source") or "") == "scheduled"

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
                    await self._update_scheduled_run(
                        schedule_run_id,
                        status="failed",
                        error_message=policy_block_message,
                        transaction_id=transaction_id,
                    )
                    await self.transaction_repo.update_status(
                        transaction_id,
                        TransactionStatusEnum.FAILED.value,
                        error_message=policy_block_message,
                    )
                    return
                await self._update_scheduled_run(
                    schedule_run_id,
                    status="processing",
                    transaction_id=transaction_id,
                )
            await self.transaction_repo.update_status(transaction_id, TransactionStatusEnum.PROCESSING.value)

            amount = airtime_data.get("amount")
            recipient_phone = airtime_data.get("phone_number")
            network = airtime_data.get("network")
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
            provider_reference = _provider_reference(result) or request_reference
            provider_status = _provider_status(result) or None

            if result.get("success"):
                await self.transaction_repo.update_status(
                    transaction_id,
                    TransactionStatusEnum.SUCCESSFUL.value,
                    provider_transaction_id=provider_reference,
                    provider_status=provider_status,
                    provider_response=result,
                )
                logger.info("airtime_success", transaction_id=transaction_id, ref=provider_reference)
                await self._update_scheduled_run(
                    schedule_run_id,
                    status="successful",
                    transaction_id=transaction_id,
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
                            "network": network or "",
                            "reference": ref,
                        },
                        _airtime_personality_context(airtime_data, amount=amount, moment="success"),
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
            elif _provider_status_is_processing(result):
                await self.transaction_repo.update_status(
                    transaction_id,
                    TransactionStatusEnum.PROCESSING.value,
                    provider_transaction_id=provider_reference,
                    provider_status=provider_status,
                    provider_response=result,
                )
                logger.info("airtime_processing", transaction_id=transaction_id, status=_provider_status(result))
                await self._update_scheduled_run(
                    schedule_run_id,
                    status="processing",
                    transaction_id=transaction_id,
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
                elif delivery_target and not is_grouped_async_message(data):
                    amount_text = f"{amount:,.2f}" if amount is not None else "0.00"
                    message = render_personalized_message(
                        "airtime.execution.message_queued",
                        locale,
                        {
                            "amount": amount_text,
                            "recipient_phone": recipient_phone or "",
                            "network": network or "",
                        },
                        _airtime_personality_context(airtime_data, amount=amount, moment="pending"),
                    )
                    await self.delivery_service.deliver_text(
                        phone_number=delivery_target,
                        channel=channel,
                        text=message,
                        metadata={"source": "airtime_executor", "transaction_id": transaction_id},
                        dedupe_key=f"airtime:processing:{transaction_id}",
                    )
                else:
                    logger.warning("airtime_delivery_target_missing", transaction_id=transaction_id, channel=channel)
            else:
                error_msg = _provider_error_message(
                    result,
                    render_message("airtime.error.provider_failed", locale),
                )
                await self.transaction_repo.update_status(
                    transaction_id,
                    TransactionStatusEnum.FAILED.value,
                    error_message=error_msg,
                    provider_transaction_id=_provider_reference(result),
                    provider_status=provider_status,
                    provider_response=result,
                    provider_error_code=_provider_error_code(result),
                )
                logger.error("airtime_failed", transaction_id=transaction_id, error=error_msg)
                await self._update_scheduled_run(
                    schedule_run_id,
                    status="failed",
                    error_message=error_msg,
                    transaction_id=transaction_id,
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
                        code=_provider_error_code(result),
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
                            "network": network or "",
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
            await self._update_scheduled_run(
                schedule_run_id,
                status="failed",
                error_message=error_msg,
                transaction_id=transaction_id,
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
