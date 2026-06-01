"""User-facing completion notifications for debit-backed bill payments."""

from __future__ import annotations

from typing import Any, Literal

import banking.transactions.runtime.scheduled_runs as scheduled_runs
from banking.beneficiaries.services.post_transaction_beneficiary import (
    BeneficiarySuggestionServiceProtocol,
    append_beneficiary_suggestion,
    suggest_mobile_beneficiary,
)
from banking.messaging.delivery.service import DeliveryService
from banking.persistence.unit_of_work import UnitOfWork
from banking.presentation.i18n.personality import PersonalityContext, render_personalized_message
from banking.presentation.i18n.renderer import render_message, render_text
from banking.transactions.runtime.async_completion import (
    is_grouped_async_message,
    record_group_leg_and_maybe_build_summary,
)
from banking.transactions.runtime.async_group_types import AsyncGroupRedis
from banking.transactions.runtime.failure_categories import classify_failure_category
from shared.database.enums import TransactionTypeEnum
from shared.money import MoneyAmount, naira_to_json, to_naira
from shared.utils.logging import get_logger
from shared.utils.network_utils import format_network_display_name

logger = get_logger(__name__)

COMPLETION_CONTEXT_KEY = "completion_context"
BillCompletionEvent = Literal[
    "successful",
    "processing",
    "failed_refund_pending",
    "refunded",
    "refund_failed",
    "debit_failed",
]


def _compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, bool):
            if value:
                compact[key] = value
            continue
        if value not in (None, "", [], {}):
            compact[key] = value
    return compact


def _safe_meta(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _delivery_target(message: dict[str, Any]) -> str:
    return str(message.get("channel_identity") or message.get("phone_number") or "").strip()


def _airtime_recipient_target(data: dict[str, Any], recipient_phone: Any, locale: str) -> str:
    phone = str(recipient_phone or "").strip()
    if data.get("is_self"):
        self_label = render_message("airtime.format.summary.target_self", locale)
        return f"{self_label} ({phone})" if phone else self_label
    recipient_name = str(data.get("recipient_name") or data.get("name") or "").strip()
    if recipient_name:
        return f"{recipient_name} ({phone})" if phone else recipient_name
    return phone


def _data_recipient_display(data: dict[str, Any], recipient_phone: Any) -> str:
    if data.get("is_self"):
        return "My Number"
    recipient_name = str(data.get("recipient_name") or data.get("name") or "").strip()
    if recipient_name:
        return recipient_name
    return str(recipient_phone or "").strip()


def _personality_context(
    data: dict[str, Any],
    *,
    amount: MoneyAmount | int | str | None,
    moment: Literal["success", "failure", "pending"],
) -> PersonalityContext:
    return PersonalityContext(
        moment=moment,
        amount=to_naira(amount),
        saved_recipient=bool(data.get("beneficiary_id") or data.get("is_self")),
    )


def build_airtime_completion_context(
    *,
    message: dict[str, Any],
    airtime_data: dict[str, Any],
    amount: MoneyAmount | int | str | None,
) -> dict[str, Any]:
    """Build durable, non-secret notification context for an airtime transaction."""
    return _compact_dict(
        {
            "domain": TransactionTypeEnum.AIRTIME.value,
            "phone_number": message.get("phone_number"),
            "channel": message.get("channel"),
            "channel_identity": message.get("channel_identity"),
            "language": message.get("language"),
            "async_group": message.get("async_group"),
            "scheduled_meta": message.get("scheduled_meta"),
            "amount_naira": naira_to_json(amount),
            "recipient_phone": airtime_data.get("phone_number"),
            "network": airtime_data.get("network"),
            "recipient_name": airtime_data.get("recipient_name") or airtime_data.get("name"),
            "beneficiary_id": airtime_data.get("beneficiary_id"),
            "is_self": airtime_data.get("is_self"),
            "source_account_id": airtime_data.get("source_account_id"),
            "source_account_number": airtime_data.get("source_account_number"),
            "source_bank_name": airtime_data.get("source_bank_name"),
            "source_affinity_mode": airtime_data.get("source_affinity_mode"),
        }
    )


def build_data_completion_context(
    *,
    message: dict[str, Any],
    data_purchase: dict[str, Any],
    amount: MoneyAmount | int | str | None,
) -> dict[str, Any]:
    """Build durable, non-secret notification context for a data transaction."""
    return _compact_dict(
        {
            "domain": TransactionTypeEnum.DATA.value,
            "phone_number": message.get("phone_number"),
            "channel": message.get("channel"),
            "channel_identity": message.get("channel_identity"),
            "language": message.get("language"),
            "async_group": message.get("async_group"),
            "scheduled_meta": message.get("scheduled_meta"),
            "amount_naira": naira_to_json(amount),
            "plan_code": data_purchase.get("plan_code"),
            "plan_name": data_purchase.get("plan_name"),
            "biller_code": data_purchase.get("biller_code"),
            "target_phone": data_purchase.get("target_phone"),
            "network": data_purchase.get("network"),
            "recipient_name": data_purchase.get("recipient_name") or data_purchase.get("name"),
            "beneficiary_id": data_purchase.get("beneficiary_id"),
            "is_self": data_purchase.get("is_self"),
            "source": data_purchase.get("source"),
            "source_account_id": data_purchase.get("source_account_id"),
            "source_account_number": data_purchase.get("source_account_number") or data_purchase.get("source"),
            "source_bank_name": data_purchase.get("source_bank_name"),
            "source_affinity_mode": data_purchase.get("source_affinity_mode"),
        }
    )


def merge_completion_context(service_metadata: Any, completion_context: dict[str, Any]) -> dict[str, Any]:
    """Merge durable completion context into Transaction.service_metadata."""
    metadata = dict(service_metadata) if isinstance(service_metadata, dict) else {}
    metadata[COMPLETION_CONTEXT_KEY] = completion_context
    return metadata


class BillCompletionNotifier:
    """Sends channel notifications after debit-backed airtime/data state changes."""

    def __init__(
        self,
        *,
        delivery_service: DeliveryService | None = None,
        redis_client: AsyncGroupRedis | None = None,
        beneficiary_suggestion_service: BeneficiarySuggestionServiceProtocol | None = None,
    ) -> None:
        self.delivery_service = delivery_service or DeliveryService()
        self.redis_client = redis_client
        self.beneficiary_suggestion_service = beneficiary_suggestion_service

    async def notify_by_transaction_id(
        self,
        transaction_id: str,
        event: BillCompletionEvent,
        *,
        error_message: str | None = None,
    ) -> None:
        """Load a transaction and send its completion notification."""
        async with UnitOfWork() as uow:
            if not uow.transactions:
                return
            transaction = await uow.transactions.get_by_id(transaction_id)
            if not transaction:
                logger.warning("bill_completion_notification_transaction_not_found", transaction_id=transaction_id)
                return
        await self.notify(transaction, event, error_message=error_message)

    async def notify(
        self,
        transaction: Any,
        event: BillCompletionEvent,
        *,
        error_message: str | None = None,
    ) -> None:
        """Send completion notification for an already-committed transaction state."""
        try:
            await self._notify(transaction, event, error_message=error_message)
        except Exception as exc:
            logger.error(
                "bill_completion_notification_failed",
                transaction_id=str(getattr(transaction, "id", "")),
                notification_event=event,
                error=str(exc),
                exc_info=True,
            )

    async def _notify(
        self,
        transaction: Any,
        event: BillCompletionEvent,
        *,
        error_message: str | None,
    ) -> None:
        domain = str(getattr(transaction, "transaction_type", "") or "").lower()
        if domain not in {TransactionTypeEnum.AIRTIME.value, TransactionTypeEnum.DATA.value}:
            return
        context = self._completion_context(transaction)
        message = self._message_context(transaction, context)
        locale = str(context.get("language") or "en")
        transaction_id = str(getattr(transaction, "id", "") or "")
        effective_error = error_message or getattr(transaction, "error_message", None)

        await self._update_scheduled_run(
            message,
            event,
            transaction_id=transaction_id,
            error_message=effective_error,
        )

        completion_payload = self._completion_payload(
            transaction,
            context,
            event,
            error_message=effective_error,
            locale=locale,
        )
        batch_summary = await record_group_leg_and_maybe_build_summary(
            self.redis_client,
            message=message,
            task_type=domain,
            payload=completion_payload,
            locale=locale,
        )

        target = _delivery_target(message)
        if not target:
            logger.warning(
                "bill_completion_delivery_target_missing",
                transaction_id=transaction_id,
                notification_event=event,
            )
            return

        channel = str(message.get("channel") or "whatsapp")
        if batch_summary:
            await self.delivery_service.deliver_text(
                phone_number=target,
                channel=channel,
                text=batch_summary["text"],
                actionable_payload=batch_summary.get("actionable_payload"),
                metadata={
                    "source": "bill_completion_notifier",
                    "transaction_id": transaction_id,
                    "batched": True,
                    "summary_stage": batch_summary["stage"],
                },
                dedupe_key=f"{domain}:batch:{batch_summary['stage']}:{transaction_id}",
            )
            return

        if is_grouped_async_message(message):
            return

        text = await self._single_message(
            transaction,
            context,
            event,
            error_message=effective_error,
            locale=locale,
        )
        if not text:
            return
        await self.delivery_service.deliver_text(
            phone_number=target,
            channel=channel,
            text=text,
            metadata={"source": "bill_completion_notifier", "transaction_id": transaction_id},
            dedupe_key=f"{domain}:{self._dedupe_event(event)}:{transaction_id}",
        )

    @staticmethod
    def _completion_context(transaction: Any) -> dict[str, Any]:
        metadata = _safe_meta(getattr(transaction, "service_metadata", None))
        context = metadata.get(COMPLETION_CONTEXT_KEY)
        return context if isinstance(context, dict) else {}

    @staticmethod
    def _message_context(transaction: Any, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "transaction_id": str(getattr(transaction, "id", "") or ""),
            "phone_number": context.get("phone_number"),
            "channel": context.get("channel") or "whatsapp",
            "channel_identity": context.get("channel_identity"),
            "language": context.get("language") or "en",
            "async_group": context.get("async_group"),
            "scheduled_meta": context.get("scheduled_meta"),
        }

    @staticmethod
    async def _update_scheduled_run(
        message: dict[str, Any],
        event: BillCompletionEvent,
        *,
        transaction_id: str,
        error_message: str | None,
    ) -> None:
        meta = scheduled_runs.scheduled_meta(message)
        status = {
            "successful": "successful",
            "processing": "processing",
            "failed_refund_pending": "processing",
            "refunded": "failed",
            "refund_failed": "failed",
            "debit_failed": "failed",
        }[event]
        await scheduled_runs.update_scheduled_run(
            scheduled_runs.schedule_run_id(meta),
            status=status,
            error_message=error_message if status == "failed" else None,
            transaction_id=transaction_id,
            warning_event="scheduled_bill_completion_update_failed",
        )

    @staticmethod
    def _dedupe_event(event: BillCompletionEvent) -> str:
        if event == "successful":
            return "success"
        if event == "debit_failed":
            return "failed"
        return event.replace("_", "-")

    @staticmethod
    def _final_status(event: BillCompletionEvent) -> str:
        if event == "successful":
            return "success"
        if event in {"processing", "failed_refund_pending"}:
            return "processing"
        return "failed"

    def _completion_payload(
        self,
        transaction: Any,
        context: dict[str, Any],
        event: BillCompletionEvent,
        *,
        error_message: str | None,
        locale: str,
    ) -> dict[str, Any]:
        domain = str(getattr(transaction, "transaction_type", "") or "").lower()
        amount = to_naira(getattr(transaction, "amount", None)) or to_naira(context.get("amount_naira"))
        payload: dict[str, Any] = {
            "amount": amount,
            "network": getattr(transaction, "mobile_network", None) or context.get("network"),
            "source_account_id": getattr(transaction, "source_account_id", None) or context.get("source_account_id"),
            "source_account_number": getattr(transaction, "source_account_number", None)
            or context.get("source_account_number"),
            "source_bank_name": getattr(transaction, "source_bank_name", None) or context.get("source_bank_name"),
            "source_affinity_mode": context.get("source_affinity_mode"),
            "final_status": self._final_status(event),
        }
        if error_message and payload["final_status"] == "failed":
            payload["error_message"] = error_message
            payload["failure_category"] = classify_failure_category(message=error_message, context="provider")
        if domain == TransactionTypeEnum.AIRTIME.value:
            payload["recipient_phone"] = getattr(transaction, "target_phone_number", None) or context.get(
                "recipient_phone"
            )
        else:
            payload["phone_number"] = getattr(transaction, "target_phone_number", None) or context.get("target_phone")
            payload["plan_name"] = (
                getattr(transaction, "biller_item_name", None)
                or context.get("plan_name")
                or render_message("data.format.summary.plan_name_fallback", locale)
            )
        return payload

    async def _single_message(
        self,
        transaction: Any,
        context: dict[str, Any],
        event: BillCompletionEvent,
        *,
        error_message: str | None,
        locale: str,
    ) -> str | None:
        domain = str(getattr(transaction, "transaction_type", "") or "").lower()
        if domain == TransactionTypeEnum.AIRTIME.value:
            return await self._airtime_message(transaction, context, event, error_message=error_message, locale=locale)
        if domain == TransactionTypeEnum.DATA.value:
            return await self._data_message(transaction, context, event, error_message=error_message, locale=locale)
        return None

    async def _airtime_message(
        self,
        transaction: Any,
        context: dict[str, Any],
        event: BillCompletionEvent,
        *,
        error_message: str | None,
        locale: str,
    ) -> str | None:
        amount = to_naira(getattr(transaction, "amount", None)) or to_naira(context.get("amount_naira"))
        recipient_phone = getattr(transaction, "target_phone_number", None) or context.get("recipient_phone")
        network = getattr(transaction, "mobile_network", None) or context.get("network")
        network_display = format_network_display_name(network)
        recipient_target = _airtime_recipient_target(context, recipient_phone, locale)
        tx_id = str(getattr(transaction, "id", "") or "")
        if event == "successful":
            message = render_personalized_message(
                "airtime.executor.success_message",
                locale,
                {
                    "amount": f"{amount or 0:,.2f}",
                    "recipient_phone": recipient_phone or "",
                    "recipient_target": recipient_target,
                    "network": network_display,
                    "reference": getattr(transaction, "transaction_id", None)
                    or render_message("airtime.executor.reference_fallback", locale),
                },
                _personality_context(context, amount=amount, moment="success"),
            )
            return append_beneficiary_suggestion(
                message,
                await self._mobile_beneficiary_suggestion(
                    context,
                    transaction_id=tx_id,
                    beneficiary_type="airtime",
                    recipient_phone=recipient_phone,
                    network=network,
                    locale=locale,
                ),
            )
        if event == "processing":
            message_context = self._message_context(transaction, context)
            if scheduled_runs.is_scheduled_run(scheduled_runs.scheduled_meta(message_context)):
                return render_personalized_message(
                    "airtime.execution.message_queued",
                    locale,
                    {
                        "amount": f"{amount or 0:,.2f}",
                        "recipient_phone": recipient_phone or "",
                        "recipient_target": recipient_target,
                        "network": network_display,
                    },
                    _personality_context(context, amount=amount, moment="pending"),
                )
            suggestion = await self._mobile_beneficiary_suggestion(
                context,
                transaction_id=tx_id,
                beneficiary_type="airtime",
                recipient_phone=recipient_phone,
                network=network,
                locale=locale,
            )
            if not suggestion:
                return None
            return append_beneficiary_suggestion(
                render_message("airtime.execution.processing_status", locale),
                suggestion,
            )
        if event == "failed_refund_pending":
            return render_text(
                "Your airtime purchase could not be completed after your account was debited. "
                "A refund has been started and we'll update you once it is complete.",
                locale,
            )
        if event == "refunded":
            return render_text(
                "Your airtime purchase could not be completed, and the debit has been refunded to your bank account.",
                locale,
            )
        if event == "refund_failed":
            return render_text(
                "Your airtime purchase could not be completed, and the refund needs manual review. "
                "We're reviewing it and will update you.",
                locale,
            )
        reason = error_message or render_message("airtime.error.provider_failed", locale)
        return render_personalized_message(
            "airtime.executor.failure_message",
            locale,
            {
                "amount": f"{amount or 0:,.2f}",
                "recipient_phone": recipient_phone or "",
                "recipient_target": recipient_target,
                "network": network_display,
                "reason": reason,
            },
            _personality_context(context, amount=amount, moment="failure"),
        )

    async def _data_message(
        self,
        transaction: Any,
        context: dict[str, Any],
        event: BillCompletionEvent,
        *,
        error_message: str | None,
        locale: str,
    ) -> str | None:
        amount = to_naira(getattr(transaction, "amount", None)) or to_naira(context.get("amount_naira"))
        recipient_phone = getattr(transaction, "target_phone_number", None) or context.get("target_phone")
        network = getattr(transaction, "mobile_network", None) or context.get("network")
        network_display = format_network_display_name(network)
        plan_name = (
            getattr(transaction, "biller_item_name", None)
            or context.get("plan_name")
            or render_message("data.format.summary.plan_name_fallback", locale)
        )
        tx_id = str(getattr(transaction, "id", "") or "")
        if event == "successful":
            message = render_personalized_message(
                "data.completion.success_message",
                locale,
                {
                    "plan_name": plan_name,
                    "amount": f"{amount or 0:,.2f}",
                    "recipient_name": _data_recipient_display(context, recipient_phone),
                    "recipient_phone": recipient_phone or "",
                    "network": network_display,
                    "transaction_id": getattr(transaction, "transaction_id", None) or tx_id,
                },
                _personality_context(context, amount=amount, moment="success"),
            )
            return append_beneficiary_suggestion(
                message,
                await self._mobile_beneficiary_suggestion(
                    context,
                    transaction_id=tx_id,
                    beneficiary_type="data",
                    recipient_phone=recipient_phone,
                    network=network,
                    locale=locale,
                ),
            )
        if event == "processing":
            message_context = self._message_context(transaction, context)
            if scheduled_runs.is_scheduled_run(scheduled_runs.scheduled_meta(message_context)):
                return render_personalized_message(
                    "data.completion.pending_message",
                    locale,
                    {
                        "plan_name": plan_name,
                        "amount": f"{amount or 0:,.2f}",
                        "recipient_phone": recipient_phone or "",
                    },
                    _personality_context(context, amount=amount, moment="pending"),
                )
            suggestion = await self._mobile_beneficiary_suggestion(
                context,
                transaction_id=tx_id,
                beneficiary_type="data",
                recipient_phone=recipient_phone,
                network=network,
                locale=locale,
            )
            if not suggestion:
                return None
            return append_beneficiary_suggestion(
                render_message("data.completion.processing_status", locale),
                suggestion,
            )
        if event == "failed_refund_pending":
            return render_text(
                "Your data purchase could not be completed after your account was debited. "
                "A refund has been started and we'll update you once it is complete.",
                locale,
            )
        if event == "refunded":
            return render_text(
                "Your data purchase could not be completed, and the debit has been refunded to your bank account.",
                locale,
            )
        if event == "refund_failed":
            return render_text(
                "Your data purchase could not be completed, and the refund needs manual review. "
                "We're reviewing it and will update you.",
                locale,
            )
        return render_personalized_message(
            "data.completion.failed_message",
            locale,
            {"error_message": error_message or render_message("data.error.provider_failed", locale)},
            _personality_context(context, amount=amount, moment="failure"),
        )

    async def _mobile_beneficiary_suggestion(
        self,
        context: dict[str, Any],
        *,
        transaction_id: str,
        beneficiary_type: Literal["airtime", "data"],
        recipient_phone: Any,
        network: Any,
        locale: str,
    ) -> str | None:
        return await suggest_mobile_beneficiary(
            self.beneficiary_suggestion_service,
            phone_number=str(context.get("phone_number") or ""),
            channel=str(context.get("channel") or "whatsapp"),
            locale=locale,
            transaction_id=transaction_id,
            beneficiary_type=beneficiary_type,
            recipient_phone=recipient_phone,
            network=network,
            recipient_name=context.get("recipient_name"),
        )
