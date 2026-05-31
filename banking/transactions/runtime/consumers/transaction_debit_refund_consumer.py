"""Consumers for refunding Mono debits after airtime/data bill failure."""

from datetime import UTC, datetime, timedelta
from typing import Any

from banking.persistence.unit_of_work import UnitOfWork
from banking.transactions.runtime.transaction_debit_helpers import finalize_transaction_debit_refund
from shared.clients.abstractions.direct_debit import DirectDebitProvider
from shared.config.settings import settings
from shared.database.enums import TransactionDebitStepStatusEnum
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransactionDebitRefundConsumer:
    """Initiates a Mono refund for a confirmed transaction debit."""

    def __init__(self, direct_debit_provider: DirectDebitProvider):
        self.direct_debit_provider = direct_debit_provider

    async def process_job(self, payload: dict[str, Any]) -> None:
        step_id = str(payload.get("transaction_debit_step_id") or "")
        transaction_id = str(payload.get("transaction_id") or "")
        if not step_id or not transaction_id:
            logger.warning("transaction_debit_refund_job_missing_ids", payload=payload)
            return

        claim = await self._claim_refund(step_id, transaction_id, payload)
        if claim is None:
            return

        result = await self.direct_debit_provider.reverse_debit(
            str(claim["refund_reference"]),
            reason="Bill payment refund",
        )
        await self._apply_result(step_id, transaction_id, str(claim["refund_reference"]), result)

    async def _claim_refund(
        self,
        step_id: str,
        transaction_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        async with UnitOfWork() as uow:
            if not uow.transaction_debit_steps or not uow.transactions:
                return None
            step = await uow.transaction_debit_steps.get_by_id_for_update(step_id)
            tx = await uow.transactions.get_by_id_for_update(transaction_id)
            if not step or not tx:
                return None
            if step.status in (
                TransactionDebitStepStatusEnum.REFUNDED.value,
                TransactionDebitStepStatusEnum.REFUND_FAILED.value,
            ):
                return None
            if step.refund_provider_id or step.refund_initiated_at:
                return None

            refund_reference = step.provider_reference or payload.get("original_reference")
            if not refund_reference:
                await uow.transaction_debit_steps.update_status(
                    str(step.id),
                    TransactionDebitStepStatusEnum.REFUND_FAILED.value,
                    error_message="Provider reference missing for refund",
                )
                await uow.commit()
                return None

            claimed = await uow.transaction_debit_steps.claim_for_refund(
                str(step.id),
                refund_reference=str(refund_reference),
            )
            if not claimed:
                return None
            await uow.commit()
            return {"refund_reference": str(refund_reference)}
        return None

    async def _apply_result(self, step_id: str, transaction_id: str, refund_reference: str, result: Any) -> None:
        async with UnitOfWork() as uow:
            if not uow.transaction_debit_steps or not uow.transactions:
                return
            step = await uow.transaction_debit_steps.get_by_id_for_update(step_id)
            tx = await uow.transactions.get_by_id_for_update(transaction_id)
            if not step or not tx:
                return
            await finalize_transaction_debit_refund(
                uow=uow,
                debit_step=step,
                transaction=tx,
                result=result,
                refund_reference=refund_reference,
            )
            await uow.commit()


class TransactionDebitRefundReconciliationConsumer:
    """Reconciles pending transaction debit refunds."""

    def __init__(self, direct_debit_provider: DirectDebitProvider):
        self.direct_debit_provider = direct_debit_provider

    async def process_job(self, payload: dict[str, Any]) -> None:
        step_id = payload.get("transaction_debit_step_id")
        transaction_id = payload.get("transaction_id")
        if step_id and transaction_id:
            await self._reconcile_step(str(step_id), str(transaction_id))
            return
        await self._reconcile_batch(payload)

    async def _reconcile_batch(self, payload: dict[str, Any]) -> None:
        limit = int(payload.get("limit") or settings.refund_reconciliation_batch_size)
        min_age = int(payload.get("min_age_seconds") or settings.refund_reconciliation_min_age_seconds)
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=min_age)
        async with UnitOfWork() as uow:
            if not uow.transaction_debit_steps:
                return
            steps = await uow.transaction_debit_steps.get_stale_refunds(cutoff=cutoff, limit=limit)
            targets = [(str(step.id), str(step.transaction_id)) for step in steps]
        for step_id, transaction_id in targets:
            await self._reconcile_step(step_id, transaction_id)

    async def _reconcile_step(self, step_id: str, transaction_id: str) -> None:
        async with UnitOfWork() as uow:
            if not uow.transaction_debit_steps or not uow.transactions:
                return
            step = await uow.transaction_debit_steps.get_by_id_for_update(step_id)
            tx = await uow.transactions.get_by_id_for_update(transaction_id)
            if not step or not tx:
                return
            if step.status == TransactionDebitStepStatusEnum.REFUND_PENDING.value:
                if step.refund_attempt_count >= int(settings.refund_reconciliation_max_attempts):
                    await uow.transaction_debit_steps.update_status(
                        str(step.id),
                        TransactionDebitStepStatusEnum.REFUND_FAILED.value,
                        error_message="Refund reconciliation attempts exhausted",
                    )
                    await uow.commit()
                    return
                refund_reference = step.provider_reference
                if not refund_reference:
                    await uow.transaction_debit_steps.update_status(
                        str(step.id),
                        TransactionDebitStepStatusEnum.REFUND_FAILED.value,
                        error_message="Provider reference missing for refund",
                    )
                    await uow.commit()
                    return
                await uow.transaction_debit_steps.claim_for_refund(str(step.id), refund_reference=str(refund_reference))
                step.refund_attempt_count = int(step.refund_attempt_count or 0) + 1
                if uow.db:
                    uow.db.add(step)
                await uow.commit()
                result = await self.direct_debit_provider.reverse_debit(
                    str(refund_reference),
                    reason="Bill payment refund",
                )
            else:
                step.refund_attempt_count = int(step.refund_attempt_count or 0) + 1
                step.refund_last_checked_at = datetime.now(UTC).replace(tzinfo=None)
                if uow.db:
                    uow.db.add(step)
                await uow.commit()
                result = await self.direct_debit_provider.get_refund_status(
                    str(step.provider_reference or ""),
                    refund_id=str(step.refund_provider_id) if step.refund_provider_id else None,
                )

        async with UnitOfWork() as uow:
            if not uow.transaction_debit_steps or not uow.transactions:
                return
            step = await uow.transaction_debit_steps.get_by_id_for_update(step_id)
            tx = await uow.transactions.get_by_id_for_update(transaction_id)
            if not step or not tx:
                return
            await finalize_transaction_debit_refund(
                uow=uow,
                debit_step=step,
                transaction=tx,
                result=result,
                refund_reference=str(step.refund_provider_reference or step.provider_reference or ""),
            )
            await uow.commit()
