"""Mono webhook service - business logic for handling Mono events."""
from typing import Optional

from shared.cache.user_data import UserDataCache
from shared.clients.whatsapp.client import WhatsAppClient
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
        "events.mandates.debit.processing": "processing",
        "events.mandates.debit.successful": "successful",
        "events.mandates.debit.failed": "failed",
    }

    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        queue: RedisQueue,
    ):
        self.whatsapp_client = whatsapp_client
        self.queue = queue
        self.cache = UserDataCache()

    async def handle_mandate_event(self, event: str, data: dict) -> bool:
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

        with UnitOfWork() as uow:
            if not uow.accounts:
                return False
                
            account = uow.accounts.update_mandate_status(mandate_id, new_status)
            if not account:
                logger.warning("mandate_not_found", mandate_id=mandate_id)
                return False

            logger.info(
                "mandate_status_updated",
                mandate_id=mandate_id,
                status=new_status,
                account_id=str(account.id),
            )

            user = uow.users.get_by_id(str(account.user_id)) if uow.users else None
            if user and user.phone_number:
                await self._invalidate_cache(user.phone_number)
                
                if new_status == "ready":
                    await self._notify_mandate_ready(
                        user.phone_number,
                        account.bank_name,
                        account.account_number,
                    )

        return True

    async def handle_debit_event(self, event: str, data: dict) -> bool:
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

        with UnitOfWork() as uow:
            if not uow.funding_steps:
                return False
                
            step = uow.funding_steps.get_by_provider_reference(reference)
            if not step:
                logger.warning("funding_step_not_found", reference=reference)
                return False

            uow.funding_steps.update_status(
                step_id=str(step.id),
                status=new_status,
                provider_response=data,
            )
            uow.commit()

            logger.info(
                "funding_step_updated",
                step_id=str(step.id),
                reference=reference,
                status=new_status,
            )

            transfer = uow.funded_transfers.get_by_id(
                str(step.funded_transfer_id)
            ) if uow.funded_transfers else None
            
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
    ) -> None:
        """Send notification when mandate is ready."""
        try:
            await self.whatsapp_client.send_text(
                to=phone_number,
                text=f"✓ Your {bank_name} account ({account_number}) is now ready for payments.",
            )
        except Exception as e:
            logger.error("mandate_ready_notification_failed", error=str(e))

    async def _check_transfer_completion(
        self,
        uow,
        transfer,
        latest_status: str,
    ) -> None:
        """Check if all debits are complete and trigger payout if so."""
        if not uow.funding_steps:
            return

        if latest_status == "failed":
            uow.funded_transfers.update_status(str(transfer.id), "refunding")
            uow.commit()
            logger.warning("transfer_failed_initiating_refund", transfer_id=str(transfer.id))
            await self._queue_refunds(uow, transfer)
            return

        if uow.funding_steps.are_all_confirmed(str(transfer.id)):
            uow.funded_transfers.update_status(str(transfer.id), "funded")
            uow.commit()
            logger.info("all_debits_complete", transfer_id=str(transfer.id))
            await self._queue_payout(transfer)

    async def _queue_refunds(self, uow, transfer) -> None:
        """Queue refund jobs for any successful funding steps."""
        successful_steps = uow.funding_steps.get_confirmed_for_transfer(str(transfer.id))
        
        if not successful_steps:
            logger.info("no_refunds_needed", transfer_id=str(transfer.id))
            uow.funded_transfers.update_status(str(transfer.id), "failed")
            uow.commit()
            return
        
        for step in successful_steps:
            try:
                await self.queue.enqueue_simple(
                    queue_name="banking:refunds",
                    message={
                        "funding_step_id": str(step.id),
                        "funded_transfer_id": str(transfer.id),
                        "amount": float(step.amount),
                        "account_id": str(step.source_account_id),
                        "original_reference": step.provider_reference,
                    },
                )
                uow.funding_steps.update_status(str(step.id), "refund_pending")
                logger.info("refund_queued", step_id=str(step.id), amount=step.amount)
            except Exception as e:
                logger.error("refund_queue_failed", step_id=str(step.id), error=str(e))
        
        uow.commit()

    async def _queue_payout(self, transfer) -> None:
        """Queue payout job after all debits complete."""
        try:
            await self.queue.enqueue_simple(
                queue_name="banking:payouts",
                message={
                    "funded_transfer_id": str(transfer.id),
                    "amount": float(transfer.transfer_amount),
                    "recipient_account": transfer.recipient_account,
                    "recipient_bank_code": transfer.recipient_bank_code,
                    "idempotency_key": transfer.idempotency_key,
                },
            )
            logger.info("payout_queued", transfer_id=str(transfer.id))
        except Exception as e:
            logger.error("payout_queue_failed", transfer_id=str(transfer.id), error=str(e))

