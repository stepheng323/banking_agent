"""Confirmation logic."""

from typing import Any

from apps.core.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.core.src.agent.graphs.transfer.pipeline.base import TransferStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.formatters.transfer import format_transfer_summary
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ConfirmationStep(TransferStep):
    """Builds confirmation request and persists token."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any,
    ) -> TransactionResult:
        if gates.confirmation_confirmed:
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})

        res = build_confirmation(data, context)

        try:
            if not worker_context.queue._redis:
                await worker_context.queue.connect()

            key = data.idempotency_key

            # Persist tokens
            await worker_context.queue._redis.setex(
                f"transfer:token:{key}:phone",
                3600,
                context.phone_number,
            )  # Also write generic transaction token if needed by unified handler
            await worker_context.queue._redis.setex(
                f"transaction:token:{key}:phone",
                3600,
                context.phone_number,
            )
        except Exception as e:
            logger.error("failed_to_persist_token", error=str(e))

        return res


def build_confirmation(
    payload: TransferPayload,
    ctx: TransferContext,
) -> TransactionResult:
    """Build confirmation summary."""
    snap = {
        "amount": payload.amount,
        "recipient_name": payload.recipient_resolved_name or payload.recipient_name,
        "recipient_bank": payload.recipient_bank_name,
        "recipient_account": payload.recipient_account,
        "source_bank": payload.source_bank_name,
        "source_account": payload.source_account_number,
        "narration": payload.narration,
    }
    summary = format_transfer_summary(
        {
            "amount": payload.amount,
            "recipientName": payload.recipient_resolved_name or payload.recipient_name,
            "recipientBank": payload.recipient_bank_name,
            "recipientAccount": payload.recipient_account,
            "sourceBank": payload.source_bank_name,
            "sourceAccount": payload.source_account_number,
            "narration": payload.narration,
        }
    )

    return TransactionResult(
        outcome=TransactionOutcome.NEEDS_CONFIRMATION,
        confirmation_snapshot=snap,
        confirmation_summary=summary,
    )
