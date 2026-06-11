"""User-facing completion notifications for async direct transfers."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

import banking.transactions.runtime.scheduled_runs as scheduled_runs
from banking.beneficiaries.services.post_transaction_beneficiary import (
    BeneficiarySuggestionServiceProtocol,
    suggest_transfer_beneficiary,
)
from banking.messaging.delivery.service import DeliveryService
from banking.presentation.formatters.transfer_notifications import (
    format_transfer_pending_message,
    format_transfer_success_message,
)
from banking.presentation.i18n.personality import render_personalized_message, transfer_personality_context_from_payload
from banking.presentation.i18n.renderer import render_message
from banking.receipts.choice import build_receipt_choice_intent
from banking.transactions.runtime.async_completion import (
    is_grouped_async_message,
    record_group_leg_and_maybe_build_summary,
)
from banking.transactions.runtime.async_group_types import AsyncGroupRedis, AsyncGroupSummaryResult
from banking.transactions.runtime.failure_categories import classify_failure_category
from banking.transactions.runtime.personality_enrichment import enrich_transfer_personality_context
from shared.money import naira_to_json, to_naira
from shared.utils.logging import get_logger

logger = get_logger(__name__)

COMPLETION_CONTEXT_KEY = "completion_context"
TransferCompletionEvent = Literal["successful", "processing", "failed", "refunded"]


def _safe_meta(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _delivery_target(message: dict[str, Any]) -> str:
    return str(message.get("channel_identity") or message.get("phone_number") or "").strip()


class TransferCompletionNotifier:
    """Sends channel notifications after direct-transfer async state changes."""

    def __init__(
        self,
        *,
        delivery_service: DeliveryService | None = None,
        redis_client: AsyncGroupRedis | None = None,
        beneficiary_suggestion_service: BeneficiarySuggestionServiceProtocol | None = None,
        transaction_repo: Any | None = None,
        funded_transfer_repo: Any | None = None,
    ) -> None:
        self.delivery_service = delivery_service or DeliveryService()
        self.redis_client = redis_client
        self.beneficiary_suggestion_service = beneficiary_suggestion_service
        self.transaction_repo = transaction_repo
        self.funded_transfer_repo = funded_transfer_repo

    async def notify(
        self,
        transaction: Any,
        event: TransferCompletionEvent,
        *,
        error_message: str | None = None,
    ) -> None:
        """Send completion notification for an already-committed transaction state."""
        try:
            await self._notify(transaction, event, error_message=error_message)
        except Exception as exc:
            logger.error(
                "transfer_completion_notification_failed",
                transaction_id=str(getattr(transaction, "id", "")),
                notification_event=event,
                error=str(exc),
                exc_info=True,
            )

    async def _notify(
        self,
        transaction: Any,
        event: TransferCompletionEvent,
        *,
        error_message: str | None,
    ) -> None:
        context = self._completion_context(transaction)
        message = self._message_context(transaction, context)
        transaction_id = str(getattr(transaction, "id", "") or "")
        locale = str(message.get("language") or "en")
        effective_error = error_message or getattr(transaction, "error_message", None)

        await self._update_scheduled_run(
            message,
            event,
            transaction_id=transaction_id,
            error_message=effective_error,
        )

        transfer_data = self._transfer_data(transaction, context)
        if event == "refunded":
            await self._deliver_refunded(transaction, message=message, transfer_data=transfer_data, locale=locale)
            return

        completion_payload = self._completion_payload(
            transfer_data,
            final_status=self._final_status(event),
            error_message=effective_error,
        )
        batch_summary = await record_group_leg_and_maybe_build_summary(
            self.redis_client,
            message=message,
            task_type="transfer",
            payload=completion_payload,
            locale=locale,
        )

        target = _delivery_target(message)
        if not target:
            logger.warning(
                "transfer_completion_delivery_target_missing",
                transaction_id=transaction_id,
                notification_event=event,
            )
            return

        if batch_summary:
            await self._deliver_group_summary(message=message, transaction_id=transaction_id, summary=batch_summary)
            return

        if is_grouped_async_message(message):
            return

        if event == "successful":
            await self._deliver_success(transaction, message=message, transfer_data=transfer_data, locale=locale)
            return
        if event == "processing":
            await self._deliver_processing(transaction, message=message, transfer_data=transfer_data, locale=locale)
            return
        await self._deliver_failed(
            transaction,
            message=message,
            transfer_data=transfer_data,
            locale=locale,
            error_message=str(effective_error or render_message("transfer.error.provider_failed", locale)),
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
        event: TransferCompletionEvent,
        *,
        transaction_id: str,
        error_message: str | None,
    ) -> None:
        status = {
            "successful": "successful",
            "processing": "processing",
            "failed": "failed",
            "refunded": "failed",
        }[event]
        await scheduled_runs.update_scheduled_run(
            scheduled_runs.schedule_run_id(scheduled_runs.scheduled_meta(message)),
            status=status,
            error_message=error_message if status == "failed" else None,
            transaction_id=transaction_id,
            warning_event="scheduled_transfer_completion_update_failed",
        )

    @staticmethod
    def _final_status(event: TransferCompletionEvent) -> str:
        if event == "successful":
            return "success"
        if event == "processing":
            return "processing"
        return "failed"

    @staticmethod
    def _transfer_data(transaction: Any, context: dict[str, Any]) -> dict[str, Any]:
        amount = to_naira(getattr(transaction, "amount", None)) or to_naira(context.get("amount_naira"))
        return {
            "amount": amount,
            "amount_naira": naira_to_json(amount),
            "recipient": {
                "name": getattr(transaction, "recipient_name", None) or context.get("recipient_name"),
                "account_number": getattr(transaction, "recipient_account_number", None)
                or context.get("recipient_account"),
                "bank_code": getattr(transaction, "recipient_bank_code", None) or context.get("recipient_bank_code"),
                "bank_name": getattr(transaction, "recipient_bank_name", None) or context.get("recipient_bank_name"),
            },
            "source": {
                "account_id": getattr(transaction, "source_account_id", None) or context.get("source_account_id"),
                "account_number": getattr(transaction, "source_account_number", None)
                or context.get("source_account_number"),
                "account_name": context.get("source_account_name"),
                "bank_name": getattr(transaction, "source_bank_name", None) or context.get("source_bank_name"),
            },
            "source_affinity_mode": context.get("source_affinity_mode"),
            "narration": getattr(transaction, "narration", None) or context.get("narration"),
        }

    @staticmethod
    def _completion_payload(
        transfer_data: dict[str, Any],
        *,
        final_status: str,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        recipient = transfer_data.get("recipient", {}) if isinstance(transfer_data.get("recipient"), dict) else {}
        source = transfer_data.get("source", {}) if isinstance(transfer_data.get("source"), dict) else {}
        payload: dict[str, Any] = {
            "amount": transfer_data.get("amount"),
            "recipient_name": recipient.get("name"),
            "recipient_resolved_name": recipient.get("name"),
            "recipient_account": recipient.get("account_number"),
            "recipient_bank_code": recipient.get("bank_code"),
            "recipient_bank_name": recipient.get("bank_name"),
            "source_account_id": source.get("account_id"),
            "source_account_number": source.get("account_number"),
            "source_bank_name": source.get("bank_name"),
            "source_affinity_mode": transfer_data.get("source_affinity_mode"),
            "narration": transfer_data.get("narration"),
            "final_status": final_status,
        }
        if error_message and final_status == "failed":
            payload["error_message"] = error_message
            payload["failure_category"] = classify_failure_category(message=error_message, context="provider")
        return payload

    async def _deliver_group_summary(
        self,
        *,
        message: dict[str, Any],
        transaction_id: str,
        summary: AsyncGroupSummaryResult,
    ) -> None:
        await self.delivery_service.deliver_text(
            phone_number=_delivery_target(message),
            channel=str(message.get("channel") or "whatsapp"),
            text=summary["text"],
            actionable_payload=summary.get("actionable_payload"),
            body_blocks=summary.get("body_blocks"),
            metadata={
                "source": "transfer_completion_notifier",
                "transaction_id": transaction_id,
                "batched": True,
                "summary_stage": summary["stage"],
            },
            dedupe_key=f"transfer:batch:{summary['stage']}:{transaction_id}",
        )

    async def _deliver_success(
        self,
        transaction: Any,
        *,
        message: dict[str, Any],
        transfer_data: dict[str, Any],
        locale: str,
    ) -> None:
        amount = to_naira(transfer_data.get("amount")) or Decimal("0.00")
        recipient = transfer_data.get("recipient", {}) if isinstance(transfer_data.get("recipient"), dict) else {}
        recipient_name = str(
            recipient.get("name") or render_message("transfer.format.summary.recipient_fallback", locale)
        )
        transaction_id = str(getattr(transaction, "id", "") or "")
        context = await enrich_transfer_personality_context(
            transfer_personality_context_from_payload(transfer_data, moment="success"),
            user_id=str(getattr(transaction, "user_id", "") or ""),
            transaction_repo=self.transaction_repo,
            funded_transfer_repo=self.funded_transfer_repo,
            payload=transfer_data,
            transaction_id=transaction_id,
            idempotency_key=getattr(transaction, "idempotency_key", None),
        )
        reference = str(
            getattr(transaction, "transaction_id", None) or getattr(transaction, "idempotency_key", None) or ""
        )
        await self.delivery_service.deliver_text(
            phone_number=_delivery_target(message),
            channel=str(message.get("channel") or "whatsapp"),
            text=format_transfer_success_message(
                amount=amount,
                recipient_name=recipient_name,
                transaction_id=reference or transaction_id,
                locale=locale,
                personality_context=context,
            ),
            metadata={"source": "transfer_completion_notifier", "transaction_id": transaction_id},
            dedupe_key=f"transfer:success:{transaction_id}",
        )
        await self._offer_receipt(message=message, transfer_data=transfer_data, transaction=transaction)
        await self._deliver_beneficiary_suggestion(
            message=message,
            transfer_data=transfer_data,
            transaction_id=transaction_id,
            locale=locale,
        )

    async def _deliver_processing(
        self,
        transaction: Any,
        *,
        message: dict[str, Any],
        transfer_data: dict[str, Any],
        locale: str,
    ) -> None:
        amount = to_naira(transfer_data.get("amount")) or Decimal("0.00")
        recipient = transfer_data.get("recipient", {}) if isinstance(transfer_data.get("recipient"), dict) else {}
        recipient_name = str(
            recipient.get("name") or render_message("transfer.format.summary.recipient_fallback", locale)
        )
        transaction_id = str(getattr(transaction, "id", "") or "")
        await self.delivery_service.deliver_text(
            phone_number=_delivery_target(message),
            channel=str(message.get("channel") or "whatsapp"),
            text=format_transfer_pending_message(
                amount=amount,
                recipient_name=recipient_name,
                locale=locale,
                personality_context=transfer_personality_context_from_payload(transfer_data, moment="pending"),
            ),
            metadata={"source": "transfer_completion_notifier", "transaction_id": transaction_id},
            dedupe_key=f"transfer:pending:{transaction_id}",
        )
        await self._deliver_beneficiary_suggestion(
            message=message,
            transfer_data=transfer_data,
            transaction_id=transaction_id,
            locale=locale,
        )

    async def _deliver_failed(
        self,
        transaction: Any,
        *,
        message: dict[str, Any],
        transfer_data: dict[str, Any],
        locale: str,
        error_message: str,
    ) -> None:
        transaction_id = str(getattr(transaction, "id", "") or "")
        text = render_personalized_message(
            "transfer.execution.failed",
            locale,
            {"error": error_message},
            transfer_personality_context_from_payload(transfer_data, moment="failure"),
        )
        if scheduled_runs.is_scheduled_run(scheduled_runs.scheduled_meta(message)):
            text = f"Scheduled transfer failed: {error_message}"
        await self.delivery_service.deliver_text(
            phone_number=_delivery_target(message),
            channel=str(message.get("channel") or "whatsapp"),
            text=text,
            metadata={"source": "transfer_completion_notifier", "transaction_id": transaction_id},
            dedupe_key=f"transfer:failed:{transaction_id}",
        )

    async def _deliver_refunded(
        self,
        transaction: Any,
        *,
        message: dict[str, Any],
        transfer_data: dict[str, Any],
        locale: str,
    ) -> None:
        amount = naira_to_json(to_naira(transfer_data.get("amount")) or Decimal("0.00")) or "0.00"
        transaction_id = str(getattr(transaction, "id", "") or "")
        await self.delivery_service.deliver_text(
            phone_number=_delivery_target(message),
            channel=str(message.get("channel") or "whatsapp"),
            text=render_message("support.reversal.completed", locale, {"amount": amount}),
            metadata={"source": "transfer_completion_notifier", "transaction_id": transaction_id},
            dedupe_key=f"transfer:refunded:{transaction_id}",
        )

    async def _offer_receipt(self, *, message: dict[str, Any], transfer_data: dict[str, Any], transaction: Any) -> None:
        transaction_id = str(getattr(transaction, "id", "") or "")
        receipt_job = self._receipt_job(message=message, transfer_data=transfer_data, transaction=transaction)
        await self.delivery_service.deliver_intents(
            phone_number=_delivery_target(message),
            channel=str(message.get("channel") or "whatsapp"),
            intents=[build_receipt_choice_intent(receipt_job, str(message.get("language") or "en"))],
            metadata={"source": "transfer_completion_notifier", "transaction_id": transaction_id},
            dedupe_key=f"receipt-choice:{transaction_id}",
            strict_actionable=True,
        )

    @staticmethod
    def _receipt_job(*, message: dict[str, Any], transfer_data: dict[str, Any], transaction: Any) -> dict[str, Any]:
        recipient = transfer_data.get("recipient", {}) if isinstance(transfer_data.get("recipient"), dict) else {}
        source = transfer_data.get("source", {}) if isinstance(transfer_data.get("source"), dict) else {}
        return {
            "phone_number": message.get("phone_number"),
            "channel": message.get("channel", "whatsapp"),
            "channel_identity": message.get("channel_identity"),
            "transfer_data": {
                "amount": transfer_data.get("amount"),
                "source": {
                    "name": source.get("bank_name") or source.get("name"),
                    "account_name": source.get("account_name"),
                    "account_number": source.get("account_number"),
                },
                "recipient": {
                    "name": recipient.get("name"),
                    "account_number": recipient.get("account_number"),
                    "bank_name": recipient.get("bank_name"),
                },
                "narration": transfer_data.get("narration"),
                "channel": message.get("channel", "whatsapp"),
                "session_id": getattr(transaction, "idempotency_key", None) or str(getattr(transaction, "id", "")),
                "processor_name": "Mono",
            },
            "transaction_reference": getattr(transaction, "transaction_id", None)
            or getattr(transaction, "idempotency_key", None),
            "signal_key": f"receipt-choice:{getattr(transaction, 'id', '')}",
        }

    async def _deliver_beneficiary_suggestion(
        self,
        *,
        message: dict[str, Any],
        transfer_data: dict[str, Any],
        transaction_id: str,
        locale: str,
    ) -> None:
        recipient = transfer_data.get("recipient", {}) if isinstance(transfer_data.get("recipient"), dict) else {}
        suggestion = await suggest_transfer_beneficiary(
            self.beneficiary_suggestion_service,
            phone_number=str(message.get("phone_number") or ""),
            channel=str(message.get("channel") or "whatsapp"),
            locale=locale,
            transaction_id=transaction_id,
            account_number=recipient.get("account_number"),
            bank_code=recipient.get("bank_code"),
            bank_name=recipient.get("bank_name"),
            recipient_name=recipient.get("name"),
            original_alias=recipient.get("original_alias"),
            bank_code_provider=recipient.get("bank_code_provider"),
            resolution_provider=recipient.get("resolution_provider"),
            is_self=recipient.get("is_self") or recipient.get("is_own_account"),
        )
        if not suggestion:
            return
        await self.delivery_service.deliver_text(
            phone_number=_delivery_target(message),
            channel=str(message.get("channel") or "whatsapp"),
            text=suggestion,
            metadata={"source": "beneficiary_suggestion", "transaction_id": transaction_id},
            dedupe_key=f"beneficiary-suggestion:transfer:{transaction_id}",
        )
