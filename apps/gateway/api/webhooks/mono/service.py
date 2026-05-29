"""Mono webhook service - business logic for handling Mono events."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from shared.cache.user_data import UserDataCache
from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum, TransactionStatusEnum
from shared.database.models import FundedTransfer, UserChannelIdentity
from shared.formatters.transfer_notifications import format_transfer_success_message
from shared.i18n.renderer import render_message
from shared.queue.adapter import QueuePublisher
from banking.persistence.unit_of_work import UnitOfWork
from banking.transactions.runtime.async_completion import (
    get_async_group_meta_for_transaction,
    record_group_leg_and_maybe_build_summary,
)
from banking.transactions.runtime.failure_categories import classify_failure_category
from shared.utils.logging import get_logger

if TYPE_CHECKING:
    from banking.messaging.delivery.service import DeliveryService

logger = get_logger(__name__)


class MonoWebhookService:
    """Handles business logic for Mono webhook events."""

    MANDATE_STATUS_MAP = {
        "events.mandates.approved": "approved",
        "events.mandates.ready": "ready",
        "events.mandates.rejected": "rejected",
        "events.mandate.action.cancel": "cancelled",
        "events.mandate.action.pause": "paused",
        "events.mandate.action.reinstate": "ready",
    }

    DEBIT_STATUS_MAP = {
        "events.mandates.debit.processing": FundingStepStatusEnum.PROCESSING.value,
        "events.mandates.debit.successful": FundingStepStatusEnum.CONFIRMED.value,
        "events.mandates.debit.failed": FundingStepStatusEnum.FAILED.value,
        "direct_debit.payment_successful": FundingStepStatusEnum.CONFIRMED.value,
        "direct_debit.payment_failed": FundingStepStatusEnum.FAILED.value,
        "direct_debit.payment_abandoned": FundingStepStatusEnum.FAILED.value,
        "direct_debit.payment_cancelled": FundingStepStatusEnum.FAILED.value,
        "direct_debit.payment_canceled": FundingStepStatusEnum.FAILED.value,
    }
    TRANSFER_DEBIT_STATUS_MAP = {
        "events.mandates.debit.processing": TransactionStatusEnum.PROCESSING.value,
        "events.mandates.debit.successful": TransactionStatusEnum.SUCCESSFUL.value,
        "events.mandates.debit.failed": TransactionStatusEnum.FAILED.value,
        "direct_debit.payment_successful": TransactionStatusEnum.SUCCESSFUL.value,
        "direct_debit.payment_failed": TransactionStatusEnum.FAILED.value,
        "direct_debit.payment_abandoned": TransactionStatusEnum.FAILED.value,
        "direct_debit.payment_cancelled": TransactionStatusEnum.FAILED.value,
        "direct_debit.payment_canceled": TransactionStatusEnum.FAILED.value,
    }

    def __init__(
        self,
        publisher: QueuePublisher | None = None,
        delivery_service: "DeliveryService | None" = None,
        redis_client: Any | None = None,
    ):
        if publisher is None:
            raise ValueError("publisher is required")
        self.publisher = publisher
        self.delivery_service = delivery_service
        self.cache = UserDataCache()
        self.redis_client = redis_client

    def _get_delivery_service(self) -> "DeliveryService":
        from banking.messaging.delivery.service import DeliveryService

        if self.delivery_service is None:
            self.delivery_service = DeliveryService()
        return self.delivery_service

    async def handle_mandate_event(self, event: str, data: dict[str, Any]) -> bool:
        """
        Handle mandate lifecycle events.

        Returns True if event was processed, False if ignored.
        """
        new_status = self.MANDATE_STATUS_MAP.get(event)
        if not new_status:
            logger.debug("mandate_event_ignored", event_name=event)
            return False

        mandate_id = data.get("id")
        if not mandate_id:
            logger.warning("mono_webhook_no_mandate_id", event_name=event)
            return False

        async with UnitOfWork() as uow:
            if not uow.accounts:
                return False

            account = await uow.accounts.update_mandate_status(mandate_id, new_status)
            if not account:
                logger.warning("mandate_not_found", mandate_id=mandate_id)
                return False

            logger.info(
                "mandate_status_updated",
                mandate_id=mandate_id,
                status=new_status,
                account_id=str(account.id),
            )

            user = await uow.users.get_by_id(str(account.user_id)) if uow.users else None
            if user and user.phone_number:
                await self._invalidate_cache(user.phone_number)

                if new_status == "ready":
                    # Determine user's active channel (default to whatsapp if none found)
                    channel = "whatsapp"
                    result = await uow.db.execute(
                        select(UserChannelIdentity.channel).where(UserChannelIdentity.user_id == user.id).limit(1)
                    )
                    first_channel = result.scalar_one_or_none()
                    if first_channel:
                        channel = first_channel

                    await self._notify_mandate_ready(
                        user.phone_number,
                        account.bank_name,
                        account.account_number,
                        channel=channel,
                    )

        return True

    async def handle_debit_event(self, event: str, data: dict[str, Any]) -> bool:
        """
        Handle direct debit transaction events.

        Returns True if event was processed, False if ignored.
        """
        funding_status = self.DEBIT_STATUS_MAP.get(event)
        transfer_status = self.TRANSFER_DEBIT_STATUS_MAP.get(event)
        if not funding_status or not transfer_status:
            logger.debug("debit_event_ignored", event_name=event)
            return False

        debit_data = self._debit_payload(data)
        reference = debit_data.get("reference_number") or debit_data.get("reference")
        debit_id = debit_data.get("id")
        if not reference and not debit_id:
            logger.warning("mono_webhook_no_reference", event_name=event)
            return False

        async with UnitOfWork() as uow:
            if uow.funding_steps and reference:
                step = await uow.funding_steps.get_by_provider_reference(reference)
                if step:
                    await uow.funding_steps.update_status(
                        step_id=str(step.id),
                        status=funding_status,
                    )
                    await uow.commit()

                    logger.info(
                        "funding_step_updated",
                        step_id=str(step.id),
                        reference=reference,
                        status=funding_status,
                    )

                    transfer = None
                    if uow.funded_transfers:
                        transfer = await uow.funded_transfers.get_by_id(str(step.funded_transfer_id))

                    if transfer:
                        await self._check_transfer_completion(uow, transfer, funding_status)

                    return True

            if not uow.transactions:
                return False

            tx = None
            if debit_id:
                tx = await uow.transactions.get_by_transaction_id(str(debit_id))
            if not tx and reference:
                tx = await uow.transactions.get_by_idempotency_key(str(reference))
            if not tx:
                logger.warning("mono_debit_target_not_found", reference=reference, debit_id=debit_id)
                return False

            previous_status = str(tx.status or "").lower()
            tx.status = transfer_status
            tx.provider_status = str(debit_data.get("status") or tx.provider_status or "")
            tx.provider_error_code = self._response_code(debit_data)
            tx.provider_response = data
            if debit_id:
                tx.transaction_id = str(debit_id)
            if transfer_status == TransactionStatusEnum.FAILED.value:
                tx.error_message = self._response_message(debit_data) or tx.error_message
            if transfer_status in {TransactionStatusEnum.SUCCESSFUL.value, TransactionStatusEnum.FAILED.value}:
                tx.completed_at = datetime.now(UTC).replace(tzinfo=None)
            uow.db.add(tx)
            await uow.commit()

            logger.info(
                "transfer_transaction_updated_from_mono_webhook",
                transaction_id=str(tx.id),
                reference=reference,
                debit_id=debit_id,
                status=transfer_status,
            )

            if (
                previous_status in {TransactionStatusEnum.PENDING.value, TransactionStatusEnum.PROCESSING.value}
                and transfer_status in {TransactionStatusEnum.SUCCESSFUL.value, TransactionStatusEnum.FAILED.value}
            ):
                grouped_handled = await self._maybe_notify_grouped_transfer_resolution(uow=uow, tx=tx, locale="en")
                if not grouped_handled:
                    await self._notify_transfer_resolution(uow=uow, tx=tx, locale="en")

        return True

    @staticmethod
    def _debit_payload(data: dict[str, Any]) -> dict[str, Any]:
        """Normalize Mono direct-debit and DirectPay webhook payload shapes."""
        nested = data.get("object")
        if isinstance(nested, dict):
            return {**data, **nested}
        return data

    @staticmethod
    def _response_code(data: dict[str, Any]) -> str | None:
        code = data.get("response_code")
        if code is None:
            code = data.get("responseCode")
        return None if code is None else str(code)

    @staticmethod
    def _response_message(data: dict[str, Any]) -> str | None:
        for key in ("message", "response_message", "description", "reason"):
            value = data.get(key)
            if value is not None:
                return str(value)
        return None

    async def _invalidate_cache(self, phone_number: str) -> None:
        """Invalidate user account cache."""
        try:
            await self.cache.invalidate_accounts(phone_number)
        except Exception as e:
            logger.warning("cache_invalidation_failed", error=str(e))

    async def _notify_mandate_ready(
        self,
        phone_number: str,
        bank_name: str,
        account_number: str,
        channel: str = "whatsapp",
    ) -> None:
        """Send notification when mandate is ready."""
        try:
            await self._get_delivery_service().deliver_text(
                phone_number=phone_number,
                channel=channel,
                text=f"✓ Your {bank_name} account ({account_number}) is now ready for payments.",
                metadata={"source": "mono_webhook"},
                dedupe_key=f"mandate-ready:{phone_number}:{account_number}",
            )
        except Exception as e:
            logger.error("mandate_ready_notification_failed", error=str(e))

    async def _resolve_delivery_target(self, uow: UnitOfWork, *, user_id: str) -> tuple[str, str] | None:
        if not uow.users:
            return None
        user = await uow.users.get_by_id(user_id)
        if not user:
            return None

        result = await uow.db.execute(
            select(UserChannelIdentity.channel, UserChannelIdentity.channel_user_id)
            .where(UserChannelIdentity.user_id == user.id)
            .order_by(UserChannelIdentity.id.asc())
            .limit(1)
        )
        identity = result.first()
        if identity:
            channel, target = identity
            channel_value = str(channel or "").strip()
            target_value = str(target or "").strip()
            if channel_value and target_value:
                return channel_value, target_value

        phone_number = str(user.phone_number or "").strip()
        if phone_number:
            return "whatsapp", phone_number
        return None

    async def _notify_transfer_resolution(self, *, uow: UnitOfWork, tx: Any, locale: str) -> None:
        target = await self._resolve_delivery_target(uow, user_id=str(tx.user_id))
        if not target:
            logger.warning("transfer_resolution_notification_target_missing", transaction_id=str(tx.id))
            return

        channel, delivery_target = target
        amount = float(getattr(tx, "amount", 0) or 0)
        recipient_name = str(getattr(tx, "recipient_name", None) or "recipient")
        provider_reference = str(
            getattr(tx, "transaction_id", None) or getattr(tx, "idempotency_key", None) or getattr(tx, "id", "")
        )
        provider_response = getattr(tx, "provider_response", None)
        if not isinstance(provider_response, dict):
            provider_response = {}
        if str(getattr(tx, "status", "") or "").lower() == TransactionStatusEnum.SUCCESSFUL.value:
            text = (
                f"Update: {format_transfer_success_message(amount, recipient_name, provider_reference, locale=locale)}"
            )
        else:
            error = str(
                getattr(tx, "error_message", None)
                or self._response_message(provider_response)
                or "Provider failed"
            )
            text = render_message("transfer.execution.failed", locale, {"error": error})

        try:
            await self._get_delivery_service().deliver_text(
                phone_number=delivery_target,
                channel=channel,
                text=text,
                metadata={"source": "mono_webhook", "transaction_id": str(getattr(tx, "id", ""))},
                dedupe_key=f"transfer:webhook:{getattr(tx, 'status', '')}:{getattr(tx, 'id', '')}",
            )
        except Exception as e:
            logger.error(
                "transfer_resolution_notification_failed",
                transaction_id=str(getattr(tx, "id", "")),
                error=str(e),
            )

    @staticmethod
    def _completion_payload_from_tx(tx: Any) -> dict[str, Any]:
        status = str(getattr(tx, "status", "") or "").lower()
        payload = {
            "amount": float(getattr(tx, "amount", 0) or 0),
            "recipient_name": getattr(tx, "recipient_name", None),
            "recipient_resolved_name": getattr(tx, "recipient_name", None),
            "recipient_account": getattr(tx, "recipient_account_number", None),
            "recipient_bank_code": getattr(tx, "recipient_bank_code", None),
            "recipient_bank_name": getattr(tx, "recipient_bank_name", None),
            "source_account_id": getattr(tx, "source_account_id", None),
            "source_account_number": getattr(tx, "source_account_number", None),
            "source_bank_name": getattr(tx, "source_bank_name", None),
            "narration": getattr(tx, "narration", None),
            "final_status": "success" if status == TransactionStatusEnum.SUCCESSFUL.value else "failed",
        }
        if status == TransactionStatusEnum.FAILED.value:
            error_message = getattr(tx, "error_message", None)
            payload["error_message"] = error_message
            payload["failure_category"] = classify_failure_category(
                message=str(error_message or ""),
                code=str(getattr(tx, "provider_error_code", "") or ""),
                context="provider",
            )
        return payload

    async def _maybe_notify_grouped_transfer_resolution(self, *, uow: UnitOfWork, tx: Any, locale: str) -> bool:
        meta = await get_async_group_meta_for_transaction(
            self.redis_client,
            transaction_id=str(getattr(tx, "id", "") or ""),
        )
        if not meta:
            return False

        summary = await record_group_leg_and_maybe_build_summary(
            self.redis_client,
            message={"transaction_id": str(getattr(tx, "id", "")), "async_group": meta},
            task_type="transfer",
            payload=self._completion_payload_from_tx(tx),
            locale=locale,
        )
        if not summary:
            return True
        if summary["stage"] != "final":
            return True

        target = await self._resolve_delivery_target(uow, user_id=str(tx.user_id))
        if not target:
            logger.warning("grouped_transfer_resolution_notification_target_missing", transaction_id=str(tx.id))
            return True

        channel, delivery_target = target
        try:
            await self._get_delivery_service().deliver_text(
                phone_number=delivery_target,
                channel=channel,
                text=summary["text"],
                actionable_payload=summary.get("actionable_payload"),
                metadata={
                    "source": "mono_webhook",
                    "transaction_id": str(getattr(tx, "id", "")),
                    "batched": True,
                    "summary_stage": "final",
                },
                dedupe_key=f"transfer:webhook:batch:final:{meta['async_group_id']}",
            )
        except Exception as e:
            logger.error(
                "grouped_transfer_resolution_notification_failed",
                transaction_id=str(getattr(tx, "id", "")),
                error=str(e),
            )
        return True

    async def _check_transfer_completion(
        self,
        uow: UnitOfWork,
        transfer: FundedTransfer,
        latest_status: str,
    ) -> None:
        """Check if all debits are complete and trigger payout if so."""
        if not uow.funding_steps:
            return

        has_failed_step = await uow.funding_steps.any_failed(str(transfer.id))
        if has_failed_step:
            if transfer.status not in (
                FundedTransferStatusEnum.REFUNDING.value,
                FundedTransferStatusEnum.REFUNDED.value,
            ):
                await uow.funded_transfers.update_status(
                    str(transfer.id),
                    FundedTransferStatusEnum.REFUNDING.value,
                )
                await uow.commit()
            logger.warning(
                "transfer_failed_initiating_refund",
                transfer_id=str(transfer.id),
                latest_status=latest_status,
            )
            await self._queue_refunds(uow, transfer)
            return

        if await uow.funding_steps.all_confirmed(str(transfer.id)):
            if transfer.status in (
                FundedTransferStatusEnum.PAYOUT_PENDING.value,
                FundedTransferStatusEnum.COMPLETED.value,
                FundedTransferStatusEnum.REFUNDING.value,
                FundedTransferStatusEnum.REFUNDED.value,
                FundedTransferStatusEnum.FAILED.value,
            ):
                logger.info(
                    "payout_already_queued_or_closed",
                    transfer_id=str(transfer.id),
                    status=transfer.status,
                )
                return
            await uow.funded_transfers.update_status(str(transfer.id), FundedTransferStatusEnum.PAYOUT_PENDING.value)
            await uow.commit()
            logger.info("all_debits_complete", transfer_id=str(transfer.id))
            await self._queue_payout(transfer)

    async def _queue_refunds(self, uow: UnitOfWork, transfer: FundedTransfer) -> None:
        """Queue refund jobs for any successful funding steps."""
        successful_steps = await uow.funding_steps.get_confirmed_for_transfer(str(transfer.id))

        if not successful_steps:
            steps = await uow.funding_steps.get_by_transfer(str(transfer.id))
            has_pending_refund = any(s.status == FundingStepStatusEnum.REFUND_PENDING.value for s in steps)
            if has_pending_refund:
                logger.info("refunds_already_pending", transfer_id=str(transfer.id))
                return
            logger.info("no_refunds_needed", transfer_id=str(transfer.id))
            await uow.funded_transfers.update_status(str(transfer.id), FundedTransferStatusEnum.FAILED.value)
            await uow.commit()
            return

        for step in successful_steps:
            try:
                await self.publisher.publish(
                    topic="refund.process",
                    message={
                        "funding_step_id": str(step.id),
                        "funded_transfer_id": str(transfer.id),
                        "amount": float(step.amount),
                        "account_id": str(step.account_id),
                        "original_reference": step.provider_reference,
                    },
                )
                await uow.funding_steps.update_status(str(step.id), FundingStepStatusEnum.REFUND_PENDING.value)
                logger.info("refund_queued", step_id=str(step.id), amount=step.amount)
            except Exception as e:
                logger.error("refund_queue_failed", step_id=str(step.id), error=str(e))

        await uow.commit()

    async def _queue_payout(self, transfer: FundedTransfer) -> None:
        """Queue payout job after all debits complete."""
        try:
            await self.publisher.publish(
                topic="payout.process",
                message={
                    "funded_transfer_id": str(transfer.id),
                    "amount": float(transfer.amount),
                    "recipient_account": transfer.recipient_account_number,
                    "recipient_bank_code": transfer.recipient_bank_code,
                    "recipient_bank_code_provider": transfer.payout_provider or "flutterwave",
                    "recipient_resolution_provider": transfer.payout_provider or "flutterwave",
                    "payout_provider": transfer.payout_provider or "flutterwave",
                    "idempotency_key": transfer.idempotency_key,
                },
            )
            logger.info("payout_queued", transfer_id=str(transfer.id))
        except Exception as e:
            logger.error("payout_queue_failed", transfer_id=str(transfer.id), error=str(e))
