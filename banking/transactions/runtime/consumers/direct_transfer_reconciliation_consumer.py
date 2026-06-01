"""Reconciliation for direct Mono transfer transactions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from banking.persistence.unit_of_work import UnitOfWork
from shared.clients.abstractions.direct_debit import DirectDebitProvider
from shared.config.settings import settings
from shared.database.enums import TransactionStatusEnum
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class DirectTransferReconciliationConsumer:
    """Recovers claimed direct transfers from Mono by deterministic reference."""

    def __init__(self, direct_debit_provider: DirectDebitProvider):
        self.direct_debit_provider = direct_debit_provider

    async def process_job(self, payload: dict[str, Any]) -> None:
        transaction_id = payload.get("transaction_id")
        if transaction_id:
            await self._process_one(str(transaction_id), reference=payload.get("reference"))
            return

        limit = int(payload.get("limit") or settings.direct_transfer_reconciliation_batch_size)
        min_age = int(payload.get("min_age_seconds") or settings.direct_transfer_reconciliation_min_age_seconds)
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=min_age)
        async with UnitOfWork() as uow:
            if not uow.transactions:
                return
            transactions = await uow.transactions.list_recoverable_direct_transfers(cutoff=cutoff, limit=limit)

        for tx in transactions:
            await self._process_one(str(tx.id), reference=getattr(tx, "idempotency_key", None))

    async def _process_one(self, transaction_id: str, *, reference: Any = None) -> None:
        async with UnitOfWork() as uow:
            if not uow.transactions:
                return
            tx = await uow.transactions.get_by_id_for_update(transaction_id)
            if not tx:
                logger.warning("direct_transfer_reconciliation_transaction_not_found", transaction_id=transaction_id)
                return
            if tx.status != TransactionStatusEnum.PROCESSING.value:
                return
            lookup_reference = str(reference or tx.idempotency_key or tx.transaction_id or "").strip()
            if not lookup_reference:
                return

        result = await self.direct_debit_provider.get_debit_status_by_reference(lookup_reference)
        async with UnitOfWork() as uow:
            if not uow.transactions:
                return
            transaction, outcome = await uow.transactions.apply_direct_transfer_result(
                transaction_id,
                result=result,
                provider_reference=lookup_reference,
            )
        logger.info(
            "direct_transfer_reconciliation_applied",
            transaction_id=transaction_id,
            outcome=outcome,
            provider_status=getattr(result.status, "value", result.status),
            found=bool(transaction),
        )
