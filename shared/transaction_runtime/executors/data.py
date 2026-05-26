"""Data Executor.

Handles execution of data transactions from the queue.
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as redis

from shared.clients.abstractions.bill import BillPaymentProvider
from shared.database.enums import TransactionStatusEnum
from shared.i18n import render_message
from shared.i18n.personality import PersonalityContext, TransferMoment, render_personalized_message
from shared.policy.service import capability_block_message
from shared.repositories.transaction_repository import TransactionRepository
from shared.repositories.unit_of_work import UnitOfWork
from shared.services.async_completion import (
    is_grouped_async_message,
    record_group_leg_and_maybe_build_summary,
)
from shared.services.delivery_service import DeliveryService
from shared.services.failure_categories import classify_failure_category
from shared.services.post_transaction_beneficiary import (
    BeneficiarySuggestionServiceProtocol,
    append_beneficiary_suggestion,
    suggest_mobile_beneficiary,
)
from shared.utils.logging import get_logger
from shared.utils.network_utils import format_network_display_name

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


def _provider_status(result: dict[str, Any]) -> str | None:
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
    return None


def _provider_status_is_processing(result: dict[str, Any]) -> bool:
    return _provider_status(result) in {"pending", "processing", "queued"}


def _execution_error_message(locale: str) -> str:
    return render_message("data.error.execution_failed", locale)


def _data_personality_context(
    data_purchase: dict[str, Any],
    *,
    amount: float | int | None,
    moment: TransferMoment,
) -> PersonalityContext:
    return PersonalityContext(
        moment=moment,
        amount=float(amount) if amount is not None else None,
        saved_recipient=bool(data_purchase.get("beneficiary_id") or data_purchase.get("is_self")),
    )


def _data_recipient_display(data_purchase: dict[str, Any], recipient_phone: Any) -> str:
    if data_purchase.get("is_self"):
        return "My Number"
    recipient_name = str(data_purchase.get("recipient_name") or data_purchase.get("name") or "").strip()
    if recipient_name:
        return recipient_name
    return str(recipient_phone or "").strip()


class DataExecutor:
    """Executor for Data transactions."""

    def __init__(
        self,
        bill_provider: BillPaymentProvider,
        transaction_repo: TransactionRepository,
        delivery_service: DeliveryService | None = None,
        redis_client: redis.Redis | None = None,
        beneficiary_suggestion_service: BeneficiarySuggestionServiceProtocol | None = None,
    ):
        self.bill_provider = bill_provider
        self.transaction_repo = transaction_repo
        self.delivery_service = delivery_service or DeliveryService()
        self.redis_client = redis_client
        self.beneficiary_suggestion_service = beneficiary_suggestion_service

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
            logger.warning("scheduled_data_run_update_failed", schedule_run_id=schedule_run_id, error=str(exc))

    async def _build_data_beneficiary_suggestion(
        self,
        *,
        data: dict[str, Any],
        data_purchase: dict[str, Any],
        transaction_id: str,
        locale: str,
    ) -> str | None:
        if is_grouped_async_message(data):
            return None
        if str(self._scheduled_meta(data).get("run_source") or "") == "scheduled":
            return None

        return await suggest_mobile_beneficiary(
            self.beneficiary_suggestion_service,
            phone_number=str(data.get("phone_number") or ""),
            channel=str(data.get("channel") or "whatsapp"),
            locale=locale,
            transaction_id=transaction_id,
            beneficiary_type="data",
            recipient_phone=data_purchase.get("target_phone"),
            network=data_purchase.get("network"),
            recipient_name=data_purchase.get("recipient_name") or data_purchase.get("name"),
        )

    async def handle_data(self, data: dict[str, Any]) -> None:
        """Handle execution of a data transaction."""
        transaction_id = data.get("transaction_id")
        data_purchase = data.get("data_purchase", {})
        locale = data.get("language", "en")
        scheduled_meta = self._scheduled_meta(data)
        schedule_run_id = str(scheduled_meta.get("schedule_run_id")) if scheduled_meta.get("schedule_run_id") else None
        is_scheduled = str(scheduled_meta.get("run_source") or "") == "scheduled"

        if not transaction_id:
            logger.error("data_execution_error", error="missing_transaction_id")
            return

        delivery_target = str(data.get("channel_identity") or data.get("phone_number") or "").strip()
        channel = str(data.get("channel") or "whatsapp")

        if policy_message := (
            capability_block_message(domain="schedule", action="schedule_data", locale=locale)
            if is_scheduled
            else None
        ) or capability_block_message(domain="data", action="buy_data", locale=locale):
            logger.info("data_execution_capability_blocked", transaction_id=transaction_id)
            await self._update_scheduled_run(
                schedule_run_id,
                status="failed",
                error_message=policy_message,
                transaction_id=transaction_id,
            )
            await self.transaction_repo.update_status(
                transaction_id,
                TransactionStatusEnum.FAILED.value,
                error_message=policy_message,
            )
            completion_payload = {
                "amount": data_purchase.get("amount"),
                "phone_number": data_purchase.get("target_phone"),
                "plan_name": data_purchase.get("plan_name"),
                "network": data_purchase.get("network"),
                "source_account_id": data_purchase.get("source_account_id"),
                "source_account_number": data_purchase.get("source_account_number") or data_purchase.get("source"),
                "source_bank_name": data_purchase.get("source_bank_name"),
                "source_affinity_mode": data_purchase.get("source_affinity_mode"),
                "final_status": "failed",
                "error_message": policy_message,
                "failure_category": "capability_blocked",
            }
            batch_summary = await record_group_leg_and_maybe_build_summary(
                self.redis_client,
                message=data,
                task_type="data",
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
                        "source": "data_executor",
                        "transaction_id": transaction_id,
                        "batched": True,
                        "summary_stage": batch_summary["stage"],
                    },
                    dedupe_key=f"data:batch:{batch_summary['stage']}:{transaction_id}",
                )
            elif delivery_target and not is_grouped_async_message(data):
                await self.delivery_service.deliver_text(
                    phone_number=delivery_target,
                    channel=channel,
                    text=render_personalized_message(
                        "data.completion.failed_message",
                        locale,
                        {"error_message": policy_message},
                        _data_personality_context(
                            data_purchase,
                            amount=data_purchase.get("amount"),
                            moment="failure",
                        ),
                    ),
                    metadata={"source": "data_executor", "transaction_id": transaction_id},
                    dedupe_key=f"data:failed:{transaction_id}",
                )
            return

        logger.info("executing_data", transaction_id=transaction_id)

        try:
            await self._update_scheduled_run(
                schedule_run_id,
                status="processing",
                transaction_id=transaction_id,
            )
            await self.transaction_repo.update_status(transaction_id, TransactionStatusEnum.PROCESSING.value)

            amount = float(data_purchase.get("amount") or 0)
            recipient_phone = data_purchase.get("target_phone")
            network = data_purchase.get("network")
            network_display = format_network_display_name(network)
            plan_code = data_purchase.get("plan_code")
            plan_name = data_purchase.get("plan_name") or render_message(
                "data.format.summary.plan_name_fallback",
                locale,
            )
            request_reference = str(data.get("idempotency_key") or transaction_id)

            if not plan_code or amount <= 0:
                error_msg = render_message("data.plan_selection.missing_plan", locale)
                await self.transaction_repo.update_status(
                    transaction_id,
                    TransactionStatusEnum.FAILED.value,
                    error_message=error_msg,
                )
                await self._update_scheduled_run(
                    schedule_run_id,
                    status="failed",
                    error_message=error_msg,
                    transaction_id=transaction_id,
                )
                if delivery_target and not is_grouped_async_message(data):
                    await self.delivery_service.deliver_text(
                        phone_number=delivery_target,
                        channel=channel,
                        text=render_personalized_message(
                            "data.completion.failed_message",
                            locale,
                            {"error_message": error_msg},
                            _data_personality_context(data_purchase, amount=amount, moment="failure"),
                        ),
                        metadata={"source": "data_executor", "transaction_id": transaction_id},
                        dedupe_key=f"data:failed:{transaction_id}",
                    )
                return

            result = await self.bill_provider.purchase_data(
                plan_code=str(plan_code or ""),
                recipient_phone=str(recipient_phone or ""),
                network=str(network or ""),
                amount=amount,
                reference=request_reference,
            )
            provider_reference = _provider_reference(result) or request_reference
            provider_status = _provider_status(result)

            if result.get("success"):
                await self.transaction_repo.update_status(
                    transaction_id,
                    TransactionStatusEnum.SUCCESSFUL.value,
                    provider_transaction_id=provider_reference,
                    provider_status=provider_status,
                    provider_response=result,
                )
                logger.info("data_success", transaction_id=transaction_id, ref=provider_reference)
                await self._update_scheduled_run(
                    schedule_run_id,
                    status="successful",
                    transaction_id=transaction_id,
                )
                completion_payload = {
                    "amount": amount,
                    "phone_number": recipient_phone,
                    "plan_name": plan_name,
                    "network": network,
                    "source_account_id": data_purchase.get("source_account_id"),
                    "source_account_number": data_purchase.get("source_account_number") or data_purchase.get("source"),
                    "source_bank_name": data_purchase.get("source_bank_name"),
                    "source_affinity_mode": data_purchase.get("source_affinity_mode"),
                    "final_status": "success",
                }
                batch_summary = await record_group_leg_and_maybe_build_summary(
                    self.redis_client,
                    message=data,
                    task_type="data",
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
                            "source": "data_executor",
                            "transaction_id": transaction_id,
                            "batched": True,
                            "summary_stage": batch_summary["stage"],
                        },
                        dedupe_key=f"data:batch:{batch_summary['stage']}:{transaction_id}",
                    )
                elif delivery_target and not is_grouped_async_message(data):
                    message = render_personalized_message(
                        "data.completion.success_message",
                        locale,
                        {
                            "plan_name": plan_name,
                            "amount": f"{amount:,.2f}",
                            "recipient_name": _data_recipient_display(data_purchase, recipient_phone),
                            "recipient_phone": recipient_phone or "",
                            "network": network_display,
                            "transaction_id": provider_reference or transaction_id,
                        },
                        _data_personality_context(data_purchase, amount=amount, moment="success"),
                    )
                    message = append_beneficiary_suggestion(
                        message,
                        await self._build_data_beneficiary_suggestion(
                            data=data,
                            data_purchase=data_purchase,
                            transaction_id=transaction_id,
                            locale=locale,
                        ),
                    )
                    await self.delivery_service.deliver_text(
                        phone_number=delivery_target,
                        channel=channel,
                        text=message,
                        metadata={"source": "data_executor", "transaction_id": transaction_id},
                        dedupe_key=f"data:success:{transaction_id}",
                    )
            else:
                if _provider_status_is_processing(result):
                    await self.transaction_repo.update_status(
                        transaction_id,
                        TransactionStatusEnum.PROCESSING.value,
                        provider_transaction_id=provider_reference,
                        provider_status=provider_status,
                        provider_response=result,
                    )
                    logger.info("data_processing", transaction_id=transaction_id, status=_provider_status(result))
                    await self._update_scheduled_run(
                        schedule_run_id,
                        status="processing",
                        transaction_id=transaction_id,
                    )
                    completion_payload = {
                        "amount": amount,
                        "phone_number": recipient_phone,
                        "plan_name": plan_name,
                        "network": network,
                        "source_account_id": data_purchase.get("source_account_id"),
                        "source_account_number": data_purchase.get("source_account_number")
                        or data_purchase.get("source"),
                        "source_bank_name": data_purchase.get("source_bank_name"),
                        "source_affinity_mode": data_purchase.get("source_affinity_mode"),
                        "final_status": "processing",
                    }
                    batch_summary = await record_group_leg_and_maybe_build_summary(
                        self.redis_client,
                        message=data,
                        task_type="data",
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
                                "source": "data_executor",
                                "transaction_id": transaction_id,
                                "batched": True,
                                "summary_stage": batch_summary["stage"],
                            },
                            dedupe_key=f"data:batch:{batch_summary['stage']}:{transaction_id}",
                        )
                    elif delivery_target and is_scheduled and not is_grouped_async_message(data):
                        message = render_personalized_message(
                            "data.completion.pending_message",
                            locale,
                            {
                                "plan_name": plan_name,
                                "amount": f"{amount:,.2f}",
                                "recipient_phone": recipient_phone or "",
                            },
                            _data_personality_context(data_purchase, amount=amount, moment="pending"),
                        )
                        await self.delivery_service.deliver_text(
                            phone_number=delivery_target,
                            channel=channel,
                            text=message,
                            metadata={"source": "data_executor", "transaction_id": transaction_id},
                            dedupe_key=f"data:processing:{transaction_id}",
                        )
                    elif delivery_target and not is_grouped_async_message(data):
                        suggestion = await self._build_data_beneficiary_suggestion(
                            data=data,
                            data_purchase=data_purchase,
                            transaction_id=transaction_id,
                            locale=locale,
                        )
                        if suggestion:
                            message = append_beneficiary_suggestion(
                                render_message("data.completion.processing_status", locale),
                                suggestion,
                            )
                            await self.delivery_service.deliver_text(
                                phone_number=delivery_target,
                                channel=channel,
                                text=message,
                                metadata={"source": "data_executor", "transaction_id": transaction_id},
                                dedupe_key=f"data:processing-suggestion:{transaction_id}",
                            )
                    return

                error_msg = _provider_error_message(
                    result,
                    render_message("data.error.provider_failed", locale),
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
                logger.error("data_failed", transaction_id=transaction_id, error=error_msg)
                await self._update_scheduled_run(
                    schedule_run_id,
                    status="failed",
                    error_message=error_msg,
                    transaction_id=transaction_id,
                )
                completion_payload = {
                    "amount": amount,
                    "phone_number": recipient_phone,
                    "plan_name": plan_name,
                    "network": network,
                    "source_account_id": data_purchase.get("source_account_id"),
                    "source_account_number": data_purchase.get("source_account_number") or data_purchase.get("source"),
                    "source_bank_name": data_purchase.get("source_bank_name"),
                    "source_affinity_mode": data_purchase.get("source_affinity_mode"),
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
                    task_type="data",
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
                            "source": "data_executor",
                            "transaction_id": transaction_id,
                            "batched": True,
                            "summary_stage": batch_summary["stage"],
                        },
                        dedupe_key=f"data:batch:{batch_summary['stage']}:{transaction_id}",
                    )
                elif delivery_target and not is_grouped_async_message(data):
                    await self.delivery_service.deliver_text(
                        phone_number=delivery_target,
                        channel=channel,
                        text=render_personalized_message(
                            "data.completion.failed_message",
                            locale,
                            {"error_message": error_msg},
                            _data_personality_context(data_purchase, amount=amount, moment="failure"),
                        ),
                        metadata={"source": "data_executor", "transaction_id": transaction_id},
                        dedupe_key=f"data:failed:{transaction_id}",
                    )

        except Exception as e:
            logger.error("data_execution_exception", transaction_id=transaction_id, error=str(e))
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
                "amount": data_purchase.get("amount"),
                "phone_number": data_purchase.get("target_phone"),
                "plan_name": data_purchase.get("plan_name"),
                "network": data_purchase.get("network"),
                "source_account_id": data_purchase.get("source_account_id"),
                "source_account_number": data_purchase.get("source_account_number") or data_purchase.get("source"),
                "source_bank_name": data_purchase.get("source_bank_name"),
                "source_affinity_mode": data_purchase.get("source_affinity_mode"),
                "final_status": "failed",
                "error_message": error_msg,
                "failure_category": classify_failure_category(message=str(e), context="execution"),
            }
            batch_summary = await record_group_leg_and_maybe_build_summary(
                self.redis_client,
                message=data,
                task_type="data",
                payload=completion_payload,
                locale=locale,
            )
            if batch_summary:
                delivery_target = str(data.get("channel_identity") or data.get("phone_number") or "").strip()
                if delivery_target:
                    await self.delivery_service.deliver_text(
                        phone_number=delivery_target,
                        channel=str(data.get("channel") or "whatsapp"),
                        text=batch_summary["text"],
                        actionable_payload=batch_summary.get("actionable_payload"),
                        metadata={
                            "source": "data_executor",
                            "transaction_id": transaction_id,
                            "batched": True,
                            "summary_stage": batch_summary["stage"],
                        },
                        dedupe_key=f"data:batch:{batch_summary['stage']}:{transaction_id}",
                    )
