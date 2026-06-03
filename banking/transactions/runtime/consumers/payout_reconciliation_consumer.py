"""Payout reconciliation consumer."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from banking.persistence.unit_of_work import UnitOfWork
from banking.transactions.runtime.payout_status import apply_payout_result, normalize_payout_status
from shared.clients.abstractions.payment import PayoutProvider
from shared.config.settings import settings
from shared.database.enums import FundedTransferStatusEnum, TransactionStatusEnum
from shared.money import naira_to_json, to_naira
from shared.observability.events import emit_operational_event
from shared.queue.adapter import QueuePublisher
from shared.security.redaction import redact_sensitive_identifiers
from shared.utils.json import to_json_safe_dict
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PayoutReconciliationTarget:
    """Resolved transfer target for payout reconciliation."""

    funded_transfer_id: str
    reference: str | None
    provider_transfer_id: str | None


class PayoutReconciliationConsumer:
    """Verifies pending Flutterwave payouts and applies terminal outcomes."""

    def __init__(self, payout_provider: PayoutProvider, publisher: QueuePublisher | None = None):
        self.payout_provider = payout_provider
        self.publisher = publisher

    async def process_job(self, payload: dict[str, Any]) -> None:
        """Process one explicit payout reconciliation job or a stale-pending batch."""
        if payload.get("funded_transfer_id") or payload.get("reference") or payload.get("provider_transfer_id"):
            await self._reconcile_payload(payload)
            return
        await self._reconcile_batch(payload)

    async def _reconcile_batch(self, payload: dict[str, Any]) -> None:
        limit = int(payload.get("limit") or settings.payout_reconciliation_batch_size)
        min_age_seconds = int(payload.get("min_age_seconds") or settings.payout_reconciliation_min_age_seconds)
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=min_age_seconds)

        async with UnitOfWork() as uow:
            if not uow.funded_transfers:
                return
            transfers = await uow.funded_transfers.get_stale_pending_payout(cutoff=cutoff, limit=limit)
            targets = [
                PayoutReconciliationTarget(
                    funded_transfer_id=str(transfer.id),
                    reference=str(transfer.idempotency_key),
                    provider_transfer_id=self._stored_provider_transfer_id(transfer),
                )
                for transfer in transfers
            ]
        if targets:
            emit_operational_event(
                "payout_stuck_transfers_found",
                severity="warning",
                domain="payout",
                details={"count": len(targets), "min_age_seconds": min_age_seconds},
            )

        for target in targets:
            await self._reconcile_target(target)

    async def _reconcile_payload(self, payload: dict[str, Any]) -> None:
        target = await self._resolve_target(payload)
        if not target:
            logger.warning(
                "payout_reconciliation_target_not_found",
                funded_transfer_id=payload.get("funded_transfer_id"),
                reference_hash=log_fingerprint(payload.get("reference")),
                transfer_id_hash=log_fingerprint(payload.get("provider_transfer_id")),
            )
            return
        await self._reconcile_target(target)

    async def _resolve_target(self, payload: dict[str, Any]) -> PayoutReconciliationTarget | None:
        funded_transfer_id = payload.get("funded_transfer_id")
        reference = self._string_or_none(payload.get("reference"))
        provider_transfer_id = self._string_or_none(payload.get("provider_transfer_id"))

        async with UnitOfWork() as uow:
            if not uow.funded_transfers:
                return None

            transfer = None
            if funded_transfer_id:
                transfer = await uow.funded_transfers.get_by_id(str(funded_transfer_id))
            if not transfer and provider_transfer_id:
                transfer = await uow.funded_transfers.get_by_payout_reference(provider_transfer_id)
            if not transfer and reference:
                transfer = await uow.funded_transfers.get_by_idempotency_key(reference)
            if not transfer and reference:
                transfer = await uow.funded_transfers.get_by_payout_reference(reference)
            if not transfer:
                return None

            return PayoutReconciliationTarget(
                funded_transfer_id=str(transfer.id),
                reference=reference or str(transfer.idempotency_key),
                provider_transfer_id=provider_transfer_id or self._stored_provider_transfer_id(transfer),
            )
        return None

    async def _reconcile_target(self, target: PayoutReconciliationTarget) -> None:
        async with UnitOfWork() as uow:
            if not uow.funded_transfers:
                return
            transfer = await uow.funded_transfers.get_by_id(target.funded_transfer_id)
            if not transfer:
                logger.warning("payout_reconciliation_transfer_not_found", funded_transfer_id=target.funded_transfer_id)
                return

            if transfer.status != FundedTransferStatusEnum.PAYOUT_PENDING.value:
                logger.info(
                    "payout_reconciliation_skipped_terminal_transfer",
                    funded_transfer_id=str(transfer.id),
                    status=transfer.status,
                )
                return
            if not getattr(transfer, "payout_initiated_at", None) and not (
                target.provider_transfer_id or self._stored_provider_transfer_id(transfer)
            ):
                await self._queue_unclaimed_payout(transfer)
                return

        result = await self._fetch_provider_result(target)
        async with UnitOfWork() as uow:
            if not uow.funded_transfers:
                return
            get_transfer = getattr(uow.funded_transfers, "get_by_id_for_update", uow.funded_transfers.get_by_id)
            transfer = await get_transfer(target.funded_transfer_id)
            if not transfer:
                logger.warning("payout_reconciliation_transfer_not_found", funded_transfer_id=target.funded_transfer_id)
                return
            if transfer.status != FundedTransferStatusEnum.PAYOUT_PENDING.value:
                logger.info(
                    "payout_reconciliation_skipped_terminal_transfer",
                    funded_transfer_id=str(transfer.id),
                    status=transfer.status,
                )
                return

            if not self._provider_result_matches_transfer(transfer, result):
                await self._mark_reconciliation_mismatch(uow, transfer, result)
                await uow.commit()
                return

            outcome = await apply_payout_result(
                uow=uow,
                transfer=transfer,
                result=result,
                publisher=self.publisher,
                provider_name=getattr(self.payout_provider, "provider_name", settings.payout_provider_name),
            )
            if outcome == "pending":
                transfer.payout_retry_count = int(transfer.payout_retry_count or 0) + 1
                max_retries = int(transfer.max_payout_retries or 0)
                if max_retries > 0 and transfer.payout_retry_count >= max_retries:
                    error = "Payout status still pending after maximum reconciliation attempts"
                    transfer.error_message = error
                    outcome = await apply_payout_result(
                        uow=uow,
                        transfer=transfer,
                        result={
                            **result,
                            "success": False,
                            "status": "failed",
                            "provider_status": result.get("provider_status") or result.get("status"),
                            "error": error,
                        },
                        publisher=self.publisher,
                        provider_name=getattr(self.payout_provider, "provider_name", settings.payout_provider_name),
                    )
            if uow.db is not None:
                uow.db.add(transfer)
            await uow.commit()

            logger.info(
                "payout_reconciliation_applied",
                funded_transfer_id=str(transfer.id),
                outcome=outcome,
                provider_status=result.get("provider_status") or result.get("status"),
            )
            emit_operational_event(
                "payout_reconciliation_applied",
                severity="high" if outcome == "failed" else "warning" if outcome == "pending" else "info",
                domain="payout",
                identifiers={"funded_transfer_id": transfer.id},
                details={
                    "outcome": outcome,
                    "provider_status": result.get("provider_status") or result.get("status"),
                },
            )

    async def _queue_unclaimed_payout(self, transfer: Any) -> None:
        if not self.publisher:
            logger.error("payout_reconciliation_publish_unavailable", funded_transfer_id=str(transfer.id))
            return
        amount_naira = naira_to_json(getattr(transfer, "amount", None)) or "0.00"
        payout_provider_name = getattr(transfer, "payout_provider", None) or getattr(
            self.payout_provider, "provider_name", settings.payout_provider_name
        )
        await self.publisher.publish(
            topic="payout.process",
            message={
                "funded_transfer_id": str(transfer.id),
                "amount": amount_naira,
                "amount_naira": amount_naira,
                "recipient_account": getattr(transfer, "recipient_account_number", ""),
                "recipient_bank_code": getattr(transfer, "recipient_bank_code", ""),
                "recipient_bank_code_provider": payout_provider_name,
                "recipient_resolution_provider": payout_provider_name,
                "payout_provider": payout_provider_name,
                "idempotency_key": transfer.idempotency_key,
                "narration": getattr(transfer, "narration", None),
            },
        )
        logger.info("payout_reconciliation_requeued_unclaimed_payout", funded_transfer_id=str(transfer.id))
        emit_operational_event(
            "payout_reconciliation_requeued_unclaimed_payout",
            severity="warning",
            domain="payout",
            identifiers={"funded_transfer_id": transfer.id},
        )

    async def _fetch_provider_result(self, target: PayoutReconciliationTarget) -> dict[str, Any]:
        """Fetch the authoritative payout state from Flutterwave."""
        provider_transfer_id = target.provider_transfer_id
        reference = target.reference

        result: dict[str, Any] | None = None
        if provider_transfer_id:
            result = await self.payout_provider.get_transfer_status(provider_transfer_id)
            try:
                status_code = int(result.get("status_code") or 0)
            except (TypeError, ValueError):
                status_code = 0
            if normalize_payout_status(result.get("status")) == "failed" and status_code in {400, 404} and reference:
                lookup = await self._get_transfer_by_reference(reference)
                if lookup:
                    result = lookup

        if result is None and reference:
            result = await self._get_transfer_by_reference(reference)

        if result is None:
            return {
                "success": False,
                "status": "pending",
                "provider": getattr(self.payout_provider, "provider_name", settings.payout_provider_name),
                "error": "Payout reconciliation requires provider transfer id or reference",
                "reference": reference,
            }

        return result

    async def _get_transfer_by_reference(self, reference: str) -> dict[str, Any] | None:
        lookup = getattr(self.payout_provider, "get_transfer_by_reference", None)
        if lookup is None:
            return None
        try:
            return await lookup(reference)
        except NotImplementedError:
            return None

    @staticmethod
    def _provider_result_matches_transfer(transfer: Any, result: dict[str, Any]) -> bool:
        result_reference = result.get("reference")
        if result_reference and str(result_reference) != str(transfer.idempotency_key):
            return False

        result_amount = result.get("amount_naira") or result.get("amount")
        if result_amount is not None:
            provider_amount = to_naira(result_amount)
            transfer_amount = to_naira(getattr(transfer, "amount", None))
            if provider_amount is None or transfer_amount is None:
                return False
            if abs(provider_amount - transfer_amount) > Decimal("0.01"):
                return False

        result_currency = result.get("currency")
        if result_currency and str(result_currency).upper() != str(transfer.currency or "NGN").upper():
            return False

        return True

    @staticmethod
    async def _mark_reconciliation_mismatch(uow: UnitOfWork, transfer: Any, result: dict[str, Any]) -> None:
        transfer.payout_retry_count = int(transfer.payout_retry_count or 0) + 1
        transfer.error_message = "Payout reconciliation mismatch; manual review required"
        tx = await uow.transactions.get_by_idempotency_key(transfer.idempotency_key) if uow.transactions else None
        if tx:
            tx.status = TransactionStatusEnum.PROCESSING.value
            tx.provider_status = "reconciliation_mismatch"
            tx.provider_response = redact_sensitive_identifiers(to_json_safe_dict(result))
            tx.error_message = transfer.error_message
            if uow.db is not None:
                uow.db.add(tx)
        if uow.db is not None:
            uow.db.add(transfer)
        emit_operational_event(
            "payout_reconciliation_mismatch",
            severity="critical",
            domain="payout",
            identifiers={"funded_transfer_id": transfer.id},
            details={"error_type": "provider_result_mismatch"},
        )
        logger.error(
            "payout_reconciliation_mismatch",
            funded_transfer_id=str(transfer.id),
            expected_reference_hash=log_fingerprint(transfer.idempotency_key),
            provider_reference_hash=log_fingerprint(result.get("reference")),
            provider_transfer_id_hash=log_fingerprint(result.get("transaction_id")),
        )

    @staticmethod
    def _stored_provider_transfer_id(transfer: Any) -> str | None:
        payout_reference = PayoutReconciliationConsumer._string_or_none(getattr(transfer, "payout_reference", None))
        if not payout_reference or payout_reference == str(getattr(transfer, "idempotency_key", "")):
            return None
        return payout_reference

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
