"""Refund reconciliation consumer for Mono funding refunds."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from banking.persistence.unit_of_work import UnitOfWork
from banking.transactions.runtime.funding_status import finalize_refund_state
from shared.clients.abstractions.direct_debit import DebitStatus, DirectDebitProvider
from shared.config.settings import settings
from shared.database.enums import FundingStepStatusEnum, SupportTicketPriorityEnum, SupportTicketStatusEnum
from shared.queue.adapter import QueuePublisher
from shared.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RefundReconciliationTarget:
    """Refund target for reconciliation."""

    funding_step_id: str
    funded_transfer_id: str


class RefundReconciliationConsumer:
    """Checks stuck funding refunds and closes the main transaction when recovered."""

    def __init__(self, direct_debit_provider: DirectDebitProvider, publisher: QueuePublisher | None = None):
        self.direct_debit_provider = direct_debit_provider
        self.publisher = publisher

    async def process_job(self, payload: dict[str, Any]) -> None:
        if payload.get("funding_step_id"):
            await self._reconcile_payload(payload)
            return
        await self._reconcile_batch(payload)

    async def _reconcile_batch(self, payload: dict[str, Any]) -> None:
        limit = int(payload.get("limit") or settings.refund_reconciliation_batch_size)
        min_age_seconds = int(payload.get("min_age_seconds") or settings.refund_reconciliation_min_age_seconds)
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=min_age_seconds)

        async with UnitOfWork() as uow:
            if not uow.funding_steps:
                return
            steps = await uow.funding_steps.get_stale_refunds(cutoff=cutoff, limit=limit)
            targets = [
                RefundReconciliationTarget(
                    funding_step_id=str(step.id),
                    funded_transfer_id=str(step.funded_transfer_id),
                )
                for step in steps
            ]

        for target in targets:
            await self._reconcile_target(target)

    async def _reconcile_payload(self, payload: dict[str, Any]) -> None:
        funding_step_id = str(payload.get("funding_step_id") or "")
        if not funding_step_id:
            return
        funded_transfer_id = str(payload.get("funded_transfer_id") or "")

        async with UnitOfWork() as uow:
            if not uow.funding_steps:
                return
            step = await uow.funding_steps.get_by_id(funding_step_id)
            if not step:
                logger.warning("refund_reconciliation_step_not_found", funding_step_id=funding_step_id)
                return
            target = RefundReconciliationTarget(
                funding_step_id=str(step.id),
                funded_transfer_id=funded_transfer_id or str(step.funded_transfer_id),
            )

        await self._reconcile_target(target)

    async def _reconcile_target(self, target: RefundReconciliationTarget) -> None:
        async with UnitOfWork() as uow:
            if not uow.funding_steps or not uow.funded_transfers:
                return

            get_step = getattr(uow.funding_steps, "get_by_id_for_update", uow.funding_steps.get_by_id)
            step = await get_step(target.funding_step_id)
            if not step:
                logger.warning("refund_reconciliation_step_not_found", funding_step_id=target.funding_step_id)
                return
            if step.status not in (
                FundingStepStatusEnum.REFUND_PENDING.value,
                FundingStepStatusEnum.REFUND_PROCESSING.value,
            ):
                logger.info(
                    "refund_reconciliation_skipped_terminal_step",
                    funding_step_id=str(step.id),
                    status=step.status,
                )
                return

            transfer = await uow.funded_transfers.get_by_id(str(step.funded_transfer_id))
            if not transfer:
                logger.warning("refund_reconciliation_transfer_not_found", funding_step_id=str(step.id))
                return

            refund_reference = step.provider_reference
            if not refund_reference:
                await self._mark_refund_failed(
                    uow,
                    step,
                    transfer,
                    "Provider reference missing for refund reconciliation",
                )
                await uow.commit()
                return

            if (
                step.status == FundingStepStatusEnum.REFUND_PENDING.value
                and not getattr(step, "refund_initiated_at", None)
                and not getattr(step, "refund_provider_id", None)
            ):
                await self._queue_unclaimed_refund(step, transfer)
                return

            result = await self.direct_debit_provider.get_refund_status(
                str(refund_reference),
                refund_id=getattr(step, "refund_provider_id", None),
            )
            await self._apply_result(uow, step, transfer, result)
            await uow.commit()

    async def _queue_unclaimed_refund(self, step: Any, transfer: Any) -> None:
        """Requeue a refund that has not yet been claimed by the refund worker."""
        if not self.publisher:
            logger.error("refund_reconciliation_publish_unavailable", funding_step_id=str(step.id))
            return
        await self.publisher.publish(
            topic="refund.process",
            message={
                "funding_step_id": str(step.id),
                "funded_transfer_id": str(transfer.id),
                "amount": float(getattr(step, "amount", 0.0) or 0.0),
                "account_id": str(getattr(step, "account_id", "")),
                "original_reference": getattr(step, "provider_reference", None),
            },
        )
        logger.info("refund_reconciliation_requeued_unclaimed_refund", funding_step_id=str(step.id))

    async def _apply_result(self, uow: UnitOfWork, step: Any, transfer: Any, result: Any) -> None:
        attempt_count = int(getattr(step, "refund_attempt_count", 0) or 0) + 1
        step.refund_attempt_count = attempt_count
        step.refund_last_checked_at = datetime.now(UTC).replace(tzinfo=None)
        if result.debit_id:
            step.refund_provider_id = result.debit_id
        if result.reference:
            step.refund_provider_reference = result.reference
        if result.error_message:
            step.refund_error_message = result.error_message
        if getattr(uow, "db", None) is not None:
            uow.db.add(step)

        if result.status == DebitStatus.REVERSED:
            await uow.funding_steps.update_status(str(step.id), FundingStepStatusEnum.REFUNDED.value)
            await finalize_refund_state(uow, transfer)
            logger.info("refund_reconciliation_completed", funding_step_id=str(step.id))
            return

        if result.status == DebitStatus.FAILED:
            await self._mark_refund_failed(uow, step, transfer, result.error_message or "Refund failed")
            return

        if attempt_count >= int(settings.refund_reconciliation_max_attempts):
            await self._mark_refund_failed(
                uow,
                step,
                transfer,
                "Refund status unresolved after maximum reconciliation attempts",
            )
            return

        await uow.funding_steps.update_status(
            str(step.id),
            FundingStepStatusEnum.REFUND_PROCESSING.value,
            error_message=result.error_message or "Refund still pending",
        )
        logger.info("refund_reconciliation_pending", funding_step_id=str(step.id), attempt_count=attempt_count)

    async def _mark_refund_failed(self, uow: UnitOfWork, step: Any, transfer: Any, error_message: str) -> None:
        await uow.funding_steps.update_status(
            str(step.id),
            FundingStepStatusEnum.REFUND_FAILED.value,
            error_message=error_message,
        )
        step.refund_error_message = error_message
        await self._record_manual_review(uow, transfer, step, error_message)
        await finalize_refund_state(uow, transfer)
        logger.error("refund_reconciliation_failed", funding_step_id=str(step.id), error=error_message)

    async def _record_manual_review(self, uow: UnitOfWork, transfer: Any, step: Any, error_message: str) -> None:
        """Create a support ticket when the current UoW exposes ticket storage."""
        tickets = getattr(uow, "support_tickets", None)
        if not tickets:
            logger.error(
                "refund_manual_review_required",
                funded_transfer_id=str(transfer.id),
                funding_step_id=str(step.id),
                error=error_message,
            )
            return

        existing = await tickets.get_by_transaction_ref(str(transfer.idempotency_key))
        if existing:
            return

        ticket_code = await tickets.generate_ticket_code()
        await tickets.create(
            ticket_code=ticket_code,
            user_id=str(transfer.user_id),
            channel="system",
            intent="reversal_refund",
            status=SupportTicketStatusEnum.OPEN.value,
            priority=SupportTicketPriorityEnum.HIGH.value,
            transaction_ref=str(transfer.idempotency_key),
            summary="Pooled transfer refund requires manual review",
            details={
                "funded_transfer_id": str(transfer.id),
                "funding_step_id": str(step.id),
                "error": error_message,
            },
        )
