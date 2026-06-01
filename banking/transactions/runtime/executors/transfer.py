"""Transfer Executor.

Handles execution of transfer transactions from the queue.
"""

from __future__ import annotations

import uuid
from typing import Any

import banking.transactions.runtime.scheduled_runs as scheduled_runs
from banking.accounts.repositories.account_repository import AccountRepository
from banking.beneficiaries.services.post_transaction_beneficiary import (
    BeneficiarySuggestionServiceProtocol,
    suggest_transfer_beneficiary,
)
from banking.messaging.delivery.service import DeliveryService
from banking.policy.service import capability_block_message
from banking.presentation.formatters.transfer_notifications import (
    format_transfer_pending_message,
    format_transfer_success_message,
)
from banking.presentation.i18n.personality import render_personalized_message, transfer_personality_context_from_payload
from banking.presentation.i18n.renderer import render_message
from banking.receipts.choice import build_receipt_choice_intent
from banking.transactions.repositories.transaction_repository import TransactionRepository
from banking.transactions.runtime.async_completion import (
    is_grouped_async_message,
    record_group_leg_and_maybe_build_summary,
)
from banking.transactions.runtime.async_group_types import AsyncGroupRedis, AsyncGroupSummaryResult
from banking.transactions.runtime.failure_categories import classify_failure_category
from banking.transactions.runtime.personality_enrichment import enrich_transfer_personality_context
from banking.transfers.repositories.funded_transfer_repository import FundedTransferRepository
from shared.clients.abstractions.direct_debit import DebitResult, DebitStatus, DirectDebitProvider
from shared.database.enums import TransactionStatusEnum
from shared.money import naira_to_json, to_naira
from shared.queue.adapter import QueuePublisher
from shared.utils.logging import get_logger

logger = get_logger(__name__)
_TERMINAL_TRANSACTION_STATUSES = {
    TransactionStatusEnum.SUCCESSFUL.value,
    TransactionStatusEnum.FAILED.value,
}


def _transaction_status(transaction: Any) -> str:
    return str(getattr(transaction, "status", "") or "").strip().lower()


def _transaction_provider_reference(transaction: Any) -> str:
    return str(
        getattr(transaction, "transaction_id", None)
        or getattr(transaction, "provider_transaction_id", None)
        or getattr(transaction, "provider_status", None)
        or ""
    ).strip()


def _transaction_user_id(transaction: Any) -> str | None:
    value = getattr(transaction, "user_id", None)
    text = str(value or "").strip()
    if not text or text.startswith("<"):
        return None
    return text


def _execution_error_message(locale: str) -> str:
    return render_message("transfer.error.execution_failed", locale)


class TransferExecutor:
    """Executor for Transfer transactions."""

    def __init__(
        self,
        direct_debit_provider: DirectDebitProvider,
        account_repo: AccountRepository,
        transaction_repo: TransactionRepository,
        publisher: QueuePublisher | None = None,
        delivery_service: DeliveryService | None = None,
        redis_client: AsyncGroupRedis | None = None,
        funded_transfer_repo: FundedTransferRepository | None = None,
        beneficiary_suggestion_service: BeneficiarySuggestionServiceProtocol | None = None,
    ):
        self.direct_debit_provider = direct_debit_provider
        self.account_repo = account_repo
        self.transaction_repo = transaction_repo
        self.publisher = publisher
        self.delivery_service = delivery_service
        self.redis_client = redis_client
        self.funded_transfer_repo = funded_transfer_repo
        self.beneficiary_suggestion_service = beneficiary_suggestion_service

    def _resolve_delivery_service(self) -> DeliveryService | None:
        if self.delivery_service is not None:
            return self.delivery_service
        try:
            self.delivery_service = DeliveryService()
            return self.delivery_service
        except Exception as exc:
            logger.warning("delivery_service_unavailable", error=str(exc))
            return None

    async def _notify_scheduled_failure(
        self,
        *,
        data: dict[str, Any],
        error_message: str,
    ) -> None:
        channel = str(data.get("channel") or "whatsapp")
        outbox_phone = str(data.get("channel_identity") or data.get("phone_number") or "")
        if not outbox_phone:
            return
        delivery = self._resolve_delivery_service()
        if not delivery:
            return
        try:
            await delivery.deliver_text(
                phone_number=outbox_phone,
                channel=channel,
                text=f"Scheduled transfer failed: {error_message}",
                metadata={"source": "transfer_executor", "scheduled": True},
                dedupe_key=f"scheduled-failed:{data.get('transaction_id')}",
            )
        except Exception as exc:
            logger.warning("scheduled_failure_notification_failed", error=str(exc))

    def _build_transfer_receipt_job(self, *, data: dict[str, Any], transfer_data: dict[str, Any]) -> dict[str, Any]:
        recipient_data = transfer_data.get("recipient", {}) if isinstance(transfer_data.get("recipient"), dict) else {}
        source_data = transfer_data.get("source", {}) if isinstance(transfer_data.get("source"), dict) else {}
        return {
            "phone_number": data.get("phone_number"),
            "channel": data.get("channel", "whatsapp"),
            "channel_identity": data.get("channel_identity"),
            "transfer_data": {
                "amount": transfer_data.get("amount"),
                "source": {
                    "name": source_data.get("bank_name") or source_data.get("name"),
                    "account_name": source_data.get("account_name"),
                    "account_number": source_data.get("account_number"),
                },
                "recipient": {
                    "name": recipient_data.get("name"),
                    "account_number": recipient_data.get("account_number"),
                    "bank_name": recipient_data.get("bank_name"),
                },
                "narration": transfer_data.get("narration"),
                "channel": data.get("channel", "whatsapp"),
                "session_id": data.get("idempotency_key") or data.get("transaction_id"),
                "processor_name": transfer_data.get("processor_name"),
            },
            "transaction_reference": (
                data.get("provider_transaction_id") or data.get("provider_reference") or data.get("transaction_id")
            ),
            "signal_key": f"receipt:{uuid.uuid4()}",
        }

    async def _offer_transfer_receipt_image(self, *, data: dict[str, Any], transfer_data: dict[str, Any]) -> None:
        delivery_target = str(data.get("channel_identity") or data.get("phone_number") or "").strip()
        channel = str(data.get("channel") or "whatsapp")
        if not delivery_target:
            logger.warning("receipt_choice_delivery_target_missing", transaction_id=data.get("transaction_id"))
            return

        delivery = self._resolve_delivery_service()
        if not delivery:
            return

        await delivery.deliver_intents(
            phone_number=delivery_target,
            channel=channel,
            intents=[
                build_receipt_choice_intent(
                    self._build_transfer_receipt_job(data=data, transfer_data=transfer_data),
                    str(data.get("language") or "en"),
                )
            ],
            metadata={"source": "transfer_executor", "transaction_id": data.get("transaction_id")},
            dedupe_key=f"receipt-choice:{data.get('transaction_id')}",
            strict_actionable=True,
        )

    async def _deliver_transfer_beneficiary_suggestion(
        self,
        *,
        data: dict[str, Any],
        transfer_data: dict[str, Any],
        transaction_id: str,
        locale: str,
    ) -> None:
        if is_grouped_async_message(data):
            return
        if scheduled_runs.is_scheduled_run(scheduled_runs.scheduled_meta(data)):
            return

        recipient = transfer_data.get("recipient", {}) if isinstance(transfer_data.get("recipient"), dict) else {}
        suggestion = await suggest_transfer_beneficiary(
            self.beneficiary_suggestion_service,
            phone_number=str(data.get("phone_number") or ""),
            channel=str(data.get("channel") or "whatsapp"),
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

        await self._deliver_text(
            data=data,
            text=suggestion,
            dedupe_key=f"beneficiary-suggestion:transfer:{transaction_id}",
            metadata={"source": "beneficiary_suggestion", "transaction_id": transaction_id},
        )

    async def _deliver_text(
        self,
        *,
        data: dict[str, Any],
        text: str,
        dedupe_key: str,
        metadata: dict[str, Any],
    ) -> None:
        delivery_target = str(data.get("channel_identity") or data.get("phone_number") or "").strip()
        channel = str(data.get("channel") or "whatsapp")
        if not delivery_target:
            logger.warning(
                "transfer_delivery_target_missing",
                transaction_id=data.get("transaction_id"),
                channel=channel,
            )
            return
        delivery = self._resolve_delivery_service()
        if not delivery:
            return
        await delivery.deliver_text(
            phone_number=delivery_target,
            channel=channel,
            text=text,
            metadata=metadata,
            dedupe_key=dedupe_key,
        )

    async def _deliver_group_summary(
        self,
        *,
        data: dict[str, Any],
        transaction_id: str,
        summary_result: AsyncGroupSummaryResult,
    ) -> None:
        await self._deliver_text(
            data=data,
            text=summary_result["text"],
            dedupe_key=f"transfer:batch:{summary_result['stage']}:{transaction_id}",
            metadata={
                "source": "transfer_executor",
                "transaction_id": transaction_id,
                "batched": True,
                "summary_stage": summary_result["stage"],
            },
        )

    def _completion_payload(
        self,
        *,
        transfer_data: dict[str, Any],
        final_status: str,
        error_message: str | None = None,
        failure_category: str | None = None,
    ) -> dict[str, Any]:
        recipient = transfer_data.get("recipient", {}) if isinstance(transfer_data.get("recipient"), dict) else {}
        source = transfer_data.get("source", {}) if isinstance(transfer_data.get("source"), dict) else {}
        payload = {
            "amount": transfer_data.get("amount"),
            "recipient_name": recipient.get("name"),
            "recipient_resolved_name": recipient.get("name"),
            "recipient_account": recipient.get("account_number"),
            "recipient_bank_code": recipient.get("bank_code"),
            "recipient_bank_name": recipient.get("bank_name"),
            "source_account_id": source.get("account_id"),
            "source_account_number": source.get("account_number"),
            "source_account_name": source.get("account_name"),
            "source_bank_name": source.get("bank_name"),
            "source_affinity_mode": transfer_data.get("source_affinity_mode"),
            "narration": transfer_data.get("narration"),
            "final_status": final_status,
        }
        if error_message:
            payload["error_message"] = error_message
        if failure_category:
            payload["failure_category"] = failure_category
        return payload

    @staticmethod
    def _provider_error_code(result: Any) -> str | None:
        provider_response = getattr(result, "provider_response", None)
        if isinstance(provider_response, dict):
            code = (
                provider_response.get("response_code")
                or provider_response.get("responseCode")
                or provider_response.get("error_code")
                or provider_response.get("code")
            )
            if code is not None:
                return str(code)
        return None

    @staticmethod
    def _completion_service_metadata(
        *,
        data: dict[str, Any],
        transfer_data: dict[str, Any],
        amount: Any,
    ) -> dict[str, Any]:
        recipient = transfer_data.get("recipient", {}) if isinstance(transfer_data.get("recipient"), dict) else {}
        source = transfer_data.get("source", {}) if isinstance(transfer_data.get("source"), dict) else {}
        context = {
            "domain": "transfer",
            "phone_number": data.get("phone_number"),
            "channel": data.get("channel"),
            "channel_identity": data.get("channel_identity"),
            "language": data.get("language"),
            "async_group": data.get("async_group"),
            "scheduled_meta": data.get("scheduled_meta"),
            "amount_naira": naira_to_json(amount),
            "recipient_name": recipient.get("name"),
            "recipient_account": recipient.get("account_number"),
            "recipient_bank_code": recipient.get("bank_code"),
            "recipient_bank_name": recipient.get("bank_name"),
            "source_account_id": source.get("account_id"),
            "source_account_number": source.get("account_number"),
            "source_account_name": source.get("account_name"),
            "source_bank_name": source.get("bank_name"),
            "source_affinity_mode": transfer_data.get("source_affinity_mode"),
            "narration": transfer_data.get("narration"),
        }
        return {"completion_context": {key: value for key, value in context.items() if value not in (None, "", [], {})}}

    async def _queue_direct_transfer_reconciliation(
        self,
        *,
        transaction_id: str,
        reference: str,
    ) -> None:
        if not self.publisher:
            return
        try:
            await self.publisher.publish(
                topic="direct_transfer.reconcile",
                message={"transaction_id": transaction_id, "reference": reference},
            )
        except Exception as exc:
            logger.error("direct_transfer_reconciliation_publish_failed", transaction_id=transaction_id, error=str(exc))

    async def handle_transfer(self, data: dict[str, Any]) -> None:
        """Handle execution of a transfer transaction."""
        transaction_id = data.get("transaction_id")
        transfer_data = data.get("transfer_data", {})
        locale = data.get("language", "en")
        scheduled_meta = scheduled_runs.scheduled_meta(data)
        schedule_run_id = scheduled_runs.schedule_run_id(scheduled_meta)
        attempt = int(scheduled_meta.get("attempt") or 1)
        is_scheduled = scheduled_runs.is_scheduled_run(scheduled_meta)

        if not transaction_id:
            logger.error("transfer_execution_error", error="missing_transaction_id")
            return

        logger.info("executing_transfer", transaction_id=transaction_id)

        provider_call_attempted = False
        try:
            if is_scheduled:
                policy_block_message = capability_block_message(
                    domain="schedule",
                    action="schedule_transfer",
                    locale=locale,
                ) or capability_block_message(
                    domain="transfer",
                    action="send_money",
                    locale=locale,
                )
                if policy_block_message:
                    logger.info("scheduled_transfer_execution_policy_blocked", transaction_id=transaction_id)
                    if schedule_run_id:
                        await scheduled_runs.update_scheduled_run(
                            schedule_run_id,
                            status="failed",
                            error_message=policy_block_message,
                        )
                    await self.transaction_repo.update_status(
                        transaction_id,
                        TransactionStatusEnum.FAILED.value,
                        policy_block_message,
                    )
                    await self._notify_scheduled_failure(data=data, error_message=policy_block_message)
                    return

            amount = transfer_data.get("amount_naira") or transfer_data.get("amount")
            recipient = transfer_data.get("recipient", {})
            source = transfer_data.get("source", {})
            narration = transfer_data.get("narration")
            source_account_id = source.get("account_id")
            recipient_account = recipient.get("account_number")
            recipient_bank_code = recipient.get("bank_code")
            reference = str(data.get("idempotency_key") or transaction_id)

            if not source_account_id:
                raise ValueError("missing_source_account_id")
            if not recipient_account or not recipient_bank_code:
                raise ValueError("missing_recipient_account_details")
            amount_value = to_naira(amount)
            if amount_value is None or amount_value <= 0:
                raise ValueError("invalid_transfer_amount")
            success_context = transfer_personality_context_from_payload(transfer_data, moment="success")
            pending_context = transfer_personality_context_from_payload(transfer_data, moment="pending")
            failure_context = transfer_personality_context_from_payload(transfer_data, moment="failure")

            source_account = await self.account_repo.get_by_id(str(source_account_id))
            if not source_account or not source_account.mandate_id:
                raise ValueError("source_account_mandate_not_ready")

            if schedule_run_id:
                await scheduled_runs.update_scheduled_run(schedule_run_id, status="processing")

            existing_getter = getattr(self.transaction_repo, "get_by_id", None)
            existing_transaction = await existing_getter(str(transaction_id)) if callable(existing_getter) else None
            claim_method = getattr(self.transaction_repo, "claim_for_direct_transfer", None)
            if not callable(claim_method):
                raise RuntimeError("direct_transfer_claim_unavailable")
            claimed_transaction = await claim_method(
                str(transaction_id),
                provider_reference=reference,
                service_metadata=self._completion_service_metadata(
                    data=data,
                    transfer_data=transfer_data,
                    amount=amount_value,
                ),
            )
            if not claimed_transaction:
                existing_transaction = (
                    await existing_getter(str(transaction_id)) if callable(existing_getter) else existing_transaction
                )
                existing_status = _transaction_status(existing_transaction)
                existing_provider_ref = _transaction_provider_reference(existing_transaction)
                if existing_status == TransactionStatusEnum.PROCESSING.value and existing_provider_ref:
                    await self._queue_direct_transfer_reconciliation(
                        transaction_id=str(transaction_id),
                        reference=str(getattr(existing_transaction, "idempotency_key", None) or reference),
                    )
                logger.warning(
                    "transfer_execution_duplicate_suppressed",
                    transaction_id=transaction_id,
                    status=existing_status,
                    has_provider_reference=bool(existing_provider_ref),
                )
                return

            try:
                provider_call_attempted = True
                result = await self.direct_debit_provider.initiate_debit_to_beneficiary(
                    amount=amount_value,
                    mandate_id=source_account.mandate_id,
                    reference=reference,
                    beneficiary_account=str(recipient_account),
                    beneficiary_bank_code=str(recipient_bank_code),
                    narration=str(narration or "Transfer"),
                )
            except Exception as exc:
                logger.error(
                    "direct_transfer_provider_call_failed_after_claim",
                    transaction_id=transaction_id,
                    error=str(exc),
                )
                result = DebitResult(
                    success=True,
                    status=DebitStatus.PROCESSING,
                    reference=reference,
                    amount=amount_value,
                    error_message="Provider status unavailable; reconciliation scheduled.",
                    provider_response={
                        "http_status": 0,
                        "message": "Provider status unavailable; reconciliation scheduled.",
                        "error_code": "CONNECTION_ERROR",
                    },
                )

            apply_method = getattr(self.transaction_repo, "apply_direct_transfer_result", None)
            if not callable(apply_method):
                raise RuntimeError("direct_transfer_result_application_unavailable")
            applied_transaction, applied_outcome = await apply_method(
                str(transaction_id),
                result=result,
                provider_reference=reference,
            )

            if applied_outcome == "successful":
                data["provider_reference"] = result.reference or result.debit_id
                data["provider_transaction_id"] = result.debit_id
                if schedule_run_id:
                    await scheduled_runs.update_scheduled_run(
                        schedule_run_id,
                        status="successful",
                        transaction_id=transaction_id,
                    )
                completion_payload = self._completion_payload(
                    transfer_data=transfer_data,
                    final_status="success",
                )
                batch_summary = await record_group_leg_and_maybe_build_summary(
                    self.redis_client,
                    message=data,
                    task_type="transfer",
                    payload=completion_payload,
                    locale=locale,
                )
                if batch_summary:
                    await self._deliver_group_summary(
                        data=data,
                        transaction_id=transaction_id,
                        summary_result=batch_summary,
                    )
                elif not is_grouped_async_message(data):
                    recipient_name = str(
                        recipient.get("name")
                        or render_message("transfer.format.summary.recipient_fallback", locale)
                    )
                    success_context = await enrich_transfer_personality_context(
                        success_context,
                        user_id=_transaction_user_id(applied_transaction)
                        or _transaction_user_id(existing_transaction)
                        or data.get("user_id"),
                        transaction_repo=self.transaction_repo,
                        funded_transfer_repo=self.funded_transfer_repo,
                        payload=transfer_data,
                        transaction_id=transaction_id,
                        idempotency_key=data.get("idempotency_key"),
                    )
                    await self._deliver_text(
                        data=data,
                        text=format_transfer_success_message(
                            amount=amount_value,
                            recipient_name=recipient_name,
                            transaction_id=result.debit_id or result.reference or transaction_id,
                            locale=locale,
                            personality_context=success_context,
                        ),
                        dedupe_key=f"transfer:success:{transaction_id}",
                        metadata={"source": "transfer_executor", "transaction_id": transaction_id},
                    )
                    await self._offer_transfer_receipt_image(data=data, transfer_data=transfer_data)
                    await self._deliver_transfer_beneficiary_suggestion(
                        data=data,
                        transfer_data=transfer_data,
                        transaction_id=transaction_id,
                        locale=locale,
                    )
                logger.info("transfer_success", transaction_id=transaction_id, ref=result.reference)
            elif applied_outcome == "processing":
                data["provider_reference"] = result.reference or result.debit_id
                data["provider_transaction_id"] = result.debit_id
                completion_payload = self._completion_payload(
                    transfer_data=transfer_data,
                    final_status="processing",
                )
                batch_summary = await record_group_leg_and_maybe_build_summary(
                    self.redis_client,
                    message=data,
                    task_type="transfer",
                    payload=completion_payload,
                    locale=locale,
                )
                if batch_summary:
                    await self._deliver_group_summary(
                        data=data,
                        transaction_id=transaction_id,
                        summary_result=batch_summary,
                    )
                elif not is_grouped_async_message(data):
                    recipient_name = str(
                        recipient.get("name")
                        or render_message("transfer.format.summary.recipient_fallback", locale)
                    )
                    await self._deliver_text(
                        data=data,
                        text=format_transfer_pending_message(
                            amount=amount_value,
                            recipient_name=recipient_name,
                            locale=locale,
                            personality_context=pending_context,
                        ),
                        dedupe_key=f"transfer:pending:{transaction_id}",
                        metadata={"source": "transfer_executor", "transaction_id": transaction_id},
                    )
                    await self._deliver_transfer_beneficiary_suggestion(
                        data=data,
                        transfer_data=transfer_data,
                        transaction_id=transaction_id,
                        locale=locale,
                    )
                logger.info("transfer_processing", transaction_id=transaction_id, ref=result.reference)
            elif applied_outcome == "failed":
                error_msg = result.error_message or render_personalized_message(
                    "transfer.error.provider_failed",
                    locale,
                    context=failure_context,
                )
                provider_error_code = self._provider_error_code(result)
                failure_category = classify_failure_category(
                    message=error_msg,
                    code=provider_error_code,
                    context="provider",
                )
                if schedule_run_id:
                    await scheduled_runs.update_scheduled_run(
                        schedule_run_id,
                        status="failed",
                        transaction_id=transaction_id,
                        error_message=error_msg,
                    )

                if is_scheduled and attempt < 2 and self.publisher:
                    retry_payload = dict(data)
                    retry_meta = dict(scheduled_meta)
                    retry_meta["attempt"] = attempt + 1
                    retry_payload["scheduled_meta"] = retry_meta
                    retry_payload["idempotency_key"] = f"{data.get('idempotency_key')}::retry{attempt + 1}"
                    await self.publisher.publish(topic="transaction.execute", message=retry_payload)
                    logger.info("scheduled_transfer_retry_enqueued", transaction_id=transaction_id, attempt=attempt + 1)
                elif is_scheduled:
                    await self._notify_scheduled_failure(data=data, error_message=error_msg)
                completion_payload = self._completion_payload(
                    transfer_data=transfer_data,
                    final_status="failed",
                    error_message=error_msg,
                    failure_category=failure_category,
                )
                batch_summary = await record_group_leg_and_maybe_build_summary(
                    self.redis_client,
                    message=data,
                    task_type="transfer",
                    payload=completion_payload,
                    locale=locale,
                )
                if batch_summary:
                    await self._deliver_group_summary(
                        data=data,
                        transaction_id=transaction_id,
                        summary_result=batch_summary,
                    )
                elif not is_grouped_async_message(data) and not is_scheduled:
                    await self._deliver_text(
                        data=data,
                        text=render_personalized_message(
                            "transfer.execution.failed",
                            locale,
                            {"error": error_msg},
                            failure_context,
                        ),
                        dedupe_key=f"transfer:failed:{transaction_id}",
                        metadata={"source": "transfer_executor", "transaction_id": transaction_id},
                    )
                logger.error("transfer_failed", transaction_id=transaction_id, error=error_msg)
            else:
                logger.info("transfer_execution_result_skipped", transaction_id=transaction_id, outcome=applied_outcome)

        except Exception as e:
            logger.error("transfer_execution_exception", transaction_id=transaction_id, error=str(e))
            if provider_call_attempted:
                await self._queue_direct_transfer_reconciliation(
                    transaction_id=str(transaction_id),
                    reference=str(data.get("idempotency_key") or transaction_id),
                )
                return
            error_msg = _execution_error_message(locale)
            failure_category = classify_failure_category(message=str(e), context="execution")
            await self.transaction_repo.update_status(
                transaction_id, TransactionStatusEnum.FAILED.value, error_message=error_msg
            )
            if schedule_run_id:
                await scheduled_runs.update_scheduled_run(
                    schedule_run_id,
                    status="failed",
                    transaction_id=transaction_id,
                    error_message=error_msg,
                )
            completion_payload = self._completion_payload(
                transfer_data=transfer_data,
                final_status="failed",
                error_message=error_msg,
                failure_category=failure_category,
            )
            batch_summary = await record_group_leg_and_maybe_build_summary(
                self.redis_client,
                message=data,
                task_type="transfer",
                payload=completion_payload,
                locale=locale,
            )
            if batch_summary:
                await self._deliver_group_summary(
                    data=data,
                    transaction_id=transaction_id,
                    summary_result=batch_summary,
                )
            if is_scheduled:
                await self._notify_scheduled_failure(data=data, error_message=error_msg)
            elif not is_grouped_async_message(data):
                await self._deliver_text(
                    data=data,
                    text=render_personalized_message(
                        "transfer.execution.failed",
                        locale,
                        {"error": error_msg},
                        transfer_personality_context_from_payload(transfer_data, moment="failure"),
                    ),
                    dedupe_key=f"transfer:failed:{transaction_id}",
                    metadata={"source": "transfer_executor", "transaction_id": transaction_id},
                )
