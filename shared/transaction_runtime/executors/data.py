"""Data Executor.

Handles execution of data transactions from the queue.
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as redis

from shared.clients.abstractions.bill import BillPaymentProvider
from shared.database.enums import TransactionStatusEnum
from shared.i18n import render_message
from shared.policy.service import capability_block_message
from shared.repositories.transaction_repository import TransactionRepository
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


def _execution_error_message(locale: str) -> str:
    return render_message("data.error.execution_failed", locale)


class DataExecutor:
    """Executor for Data transactions."""

    def __init__(
        self,
        bill_provider: BillPaymentProvider,
        transaction_repo: TransactionRepository,
        delivery_service: DeliveryService | None = None,
        redis_client: redis.Redis | None = None,
    ):
        self.bill_provider = bill_provider
        self.transaction_repo = transaction_repo
        self.delivery_service = delivery_service or DeliveryService()
        self.redis_client = redis_client

    async def handle_data(self, data: dict[str, Any]) -> None:
        """Handle execution of a data transaction."""
        transaction_id = data.get("transaction_id")
        data_purchase = data.get("data_purchase", {})
        locale = data.get("language", "en")

        if not transaction_id:
            logger.error("data_execution_error", error="missing_transaction_id")
            return

        delivery_target = str(data.get("channel_identity") or data.get("phone_number") or "").strip()
        channel = str(data.get("channel") or "whatsapp")

        if policy_message := capability_block_message(domain="data", action="buy_data", locale=locale):
            logger.info("data_execution_capability_blocked", transaction_id=transaction_id)
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
                    text=render_message(
                        "data.completion.failed_message",
                        locale,
                        {"error_message": policy_message},
                    ),
                    metadata={"source": "data_executor", "transaction_id": transaction_id},
                    dedupe_key=f"data:failed:{transaction_id}",
                )
            return

        logger.info("executing_data", transaction_id=transaction_id)

        try:
            await self.transaction_repo.update_status(transaction_id, TransactionStatusEnum.PROCESSING.value)

            amount = float(data_purchase.get("amount") or 0)
            recipient_phone = data_purchase.get("target_phone")
            network = data_purchase.get("network")
            plan_code = data_purchase.get("plan_code")
            plan_name = data_purchase.get("plan_name") or render_message(
                "data.format.summary.plan_name_fallback",
                locale,
            )

            result = await self.bill_provider.purchase_data(
                plan_code=str(plan_code or ""),
                recipient_phone=str(recipient_phone or ""),
                network=str(network or ""),
                reference=str(data.get("idempotency_key") or transaction_id),
            )

            if result.get("success"):
                await self.transaction_repo.update_status(transaction_id, TransactionStatusEnum.SUCCESSFUL.value)
                logger.info("data_success", transaction_id=transaction_id, ref=result.get("transaction_id"))
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
                    await self.delivery_service.deliver_text(
                        phone_number=delivery_target,
                        channel=channel,
                        text=render_message(
                            "data.completion.success_message",
                            locale,
                            {
                                "plan_name": plan_name,
                                "amount": f"{amount:,.2f}",
                                "recipient_name": "My Number" if data_purchase.get("is_self") else plan_name,
                                "recipient_phone": recipient_phone or "",
                                "network": network or "",
                                "transaction_id": result.get("transaction_id") or transaction_id,
                            },
                        ),
                        metadata={"source": "data_executor", "transaction_id": transaction_id},
                        dedupe_key=f"data:success:{transaction_id}",
                    )
            else:
                error_msg = _provider_error_message(
                    result,
                    render_message("data.error.provider_failed", locale),
                )
                await self.transaction_repo.update_status(
                    transaction_id, TransactionStatusEnum.FAILED.value, error_message=error_msg
                )
                logger.error("data_failed", transaction_id=transaction_id, error=error_msg)
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
                        text=render_message(
                            "data.completion.failed_message",
                            locale,
                            {"error_message": error_msg},
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
