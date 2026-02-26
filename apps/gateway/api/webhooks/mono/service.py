"""Mono webhook service - business logic for handling Mono events."""

from typing import Any

from shared.cache.user_data import UserDataCache
from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum
from shared.database.models import FundedTransfer
from shared.queue.messages import OUTBOX_QUEUE
from shared.queue.redis_queue import RedisQueue
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.logging import get_logger

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
    }

    def __init__(
        self,
        queue: RedisQueue,
    ):
        self.queue = queue
        self.cache = UserDataCache()

    async def handle_mandate_event(self, event: str, data: dict[str, Any]) -> bool:
        """
        Handle mandate lifecycle events.

        Returns True if event was processed, False if ignored.
        """
        new_status = self.MANDATE_STATUS_MAP.get(event)
        if not new_status:
            logger.debug("mandate_event_ignored", event=event)
            return False

        mandate_id = data.get("id")
        if not mandate_id:
            logger.warning("mono_webhook_no_mandate_id", event=event)
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
                    from sqlalchemy import select

                    from shared.database.models import UserChannelIdentity

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
        new_status = self.DEBIT_STATUS_MAP.get(event)
        if not new_status:
            logger.debug("debit_event_ignored", event=event)
            return False

        reference = data.get("reference_number")
        if not reference:
            logger.warning("mono_webhook_no_reference", event=event)
            return False

        async with UnitOfWork() as uow:
            if not uow.funding_steps:
                return False

            step = await uow.funding_steps.get_by_provider_reference(reference)
            if not step:
                logger.warning("funding_step_not_found", reference=reference)
                return False

            await uow.funding_steps.update_status(
                step_id=str(step.id),
                status=new_status,
                provider_response=data,
            )
            await uow.commit()

            logger.info(
                "funding_step_updated",
                step_id=str(step.id),
                reference=reference,
                status=new_status,
            )

            transfer = (
                await uow.funded_transfers.get_by_id(str(step.funded_transfer_id)) if uow.funded_transfers else None
            )

            if transfer:
                await self._check_transfer_completion(uow, transfer, new_status)

        return True

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
            await self.queue.enqueue(
                queue_name=OUTBOX_QUEUE,
                message={
                    "phone_number": phone_number,
                    "channel": channel,
                    "intents": [
                        {
                            "type": "say",
                            "text": f"✓ Your {bank_name} account ({account_number}) is now ready for payments.",
                        }
                    ],
                    "metadata": {"source": "mono_webhook"},
                },
            )
        except Exception as e:
            logger.error("mandate_ready_notification_failed", error=str(e))

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
                FundedTransferStatusEnum.FAILED.value,
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
            await uow.funded_transfers.update_status(str(transfer.id), FundedTransferStatusEnum.PAYOUT_PENDING.value)
            await uow.commit()
            logger.info("all_debits_complete", transfer_id=str(transfer.id))
            await self._queue_payout(transfer)

    async def _queue_refunds(self, uow: UnitOfWork, transfer: FundedTransfer) -> None:
        """Queue refund jobs for any successful funding steps."""
        successful_steps = await uow.funding_steps.get_confirmed_for_transfer(str(transfer.id))

        if not successful_steps:
            logger.info("no_refunds_needed", transfer_id=str(transfer.id))
            await uow.funded_transfers.update_status(str(transfer.id), FundedTransferStatusEnum.FAILED.value)
            await uow.commit()
            return

        for step in successful_steps:
            try:
                await self.queue.enqueue(
                    queue_name="banking:refunds",
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
            await self.queue.enqueue(
                queue_name="banking:payouts",
                message={
                    "funded_transfer_id": str(transfer.id),
                    "amount": float(transfer.amount),
                    "recipient_account": transfer.recipient_account_number,
                    "recipient_bank_code": transfer.recipient_bank_code,
                    "idempotency_key": transfer.idempotency_key,
                },
            )
            logger.info("payout_queued", transfer_id=str(transfer.id))
        except Exception as e:
            logger.error("payout_queue_failed", transfer_id=str(transfer.id), error=str(e))
