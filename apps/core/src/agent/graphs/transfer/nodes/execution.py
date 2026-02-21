"""Execution Node for Transfer Pipeline."""

from typing import Any

from apps.core.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.core.src.agent.graphs.transfer.nodes.security import require_auth
from apps.core.src.agent.graphs.transfer.pipeline.base import TransferStep
from apps.core.src.agent.orchestrator.models.domain import (
    TransactionOutcome,
    TransactionResult,
)
from shared.database.enums import TransactionStatusEnum
from shared.i18n import render_message
from shared.utils.logging import get_logger
from shared.utils.narration import format_narration

logger = get_logger(__name__)


class ExecutionStep(TransferStep):
    """Executes the transfer and creates transaction record."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any,
    ) -> TransactionResult:
        locale = context.language
        if not gates.confirmation_confirmed:
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})

        res = require_auth(gates)
        if res.outcome != TransactionOutcome.OK:
            if res.outcome == TransactionOutcome.NEEDS_AUTH:
                try:
                    key = data.idempotency_key
                    if not worker_context.queue._redis:
                        await worker_context.queue.connect()
                    await worker_context.queue._redis.setex(
                        f"transfer:token:{key}:phone",
                        3600,
                        context.phone_number,
                    )
                except Exception:
                    pass
            return res

        try:
            transaction_id = None
            key = data.idempotency_key

            # Format narration with deterministic rules
            narration = format_narration(data.narration, data.recipient_resolved_name or data.recipient_name)

            from shared.repositories.unit_of_work import UnitOfWork

            async with UnitOfWork() as uow:
                try:
                    existing = await uow.transactions.get_by_idempotency_key(key)
                    if existing:
                        transaction_id = str(existing.id)
                    else:
                        tx = await uow.transactions.create(
                            idempotency_key=key,
                            transaction_type="transfer",
                            status=TransactionStatusEnum.PENDING.value,
                            user_id=getattr(worker_context, "user_id", None),
                            amount=data.amount,
                            recipient_account_number=data.recipient_account,
                            recipient_bank_code=data.recipient_bank_code,
                            recipient_name=data.recipient_resolved_name or data.recipient_name or "",
                            recipient_bank_name=data.recipient_bank_name or "",
                            source_account_id=data.source_account_id,
                            source_account_number=data.source_account_number or "",
                            source_bank_name=data.source_bank_name or "",
                            narration=narration,
                        )
                        transaction_id = str(tx.id)
                        await uow.commit()
                        logger.info("transaction_persisted", id=transaction_id, key=key)
                except Exception as e:
                    logger.error("failed_to_persist_transaction", error=str(e))

            queue = worker_context.queue
            if data.funding_plan and not data.funding_plan.get("is_single_source", True):
                await queue.enqueue(
                    queue_name="payouts",
                    message={
                        "type": "payout",
                        "idempotency_key": key,
                        "transaction_id": transaction_id,
                        "funding_plan": data.funding_plan,
                        "recipient_account": data.recipient_account,
                        "recipient_bank_code": data.recipient_bank_code,
                        "narration": narration,
                    },
                )
            else:
                await queue.enqueue(
                    queue_name="transfers",
                    message={
                        "type": "execute_transfer",
                        "idempotency_key": key,
                        "transaction_id": transaction_id,
                        "phone_number": context.phone_number,
                        "transfer_data": {
                            "amount": data.amount,
                            "recipient": {
                                "account_number": data.recipient_account,
                                "bank_code": data.recipient_bank_code,
                            },
                            "source": {
                                "account_id": data.source_account_id,
                                "account_number": data.source_account_number,
                            },
                            "narration": narration,
                        },
                    },
                )

            receipt_data = {
                "status": "queued",
                "id": key,
                "amount": data.amount,
                "recipient_account": data.recipient_account,
                "recipient_bank_code": data.recipient_bank_code,
                "recipient_name": data.recipient_resolved_name or data.recipient_name,
                "narration": narration,
                "date": render_message("transfer.execution.date_now", locale),
            }
            if data.source_bank_name:
                receipt_data["source_bank"] = data.source_bank_name

            return TransactionResult(
                outcome=TransactionOutcome.OK,
                receipt=receipt_data,
                patch={"transaction_id": transaction_id} if transaction_id else {},
            )

        except Exception as e:
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("transfer.execution.failed", locale, {"error": str(e)}),
                retryable=True,
            )
