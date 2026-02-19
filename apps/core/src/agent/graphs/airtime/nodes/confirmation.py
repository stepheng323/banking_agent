"""Airtime confirmation step."""

from typing import Any

from apps.core.src.agent.graphs.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from apps.core.src.agent.graphs.airtime.pipeline.base import AirtimeStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.formatters.airtime import format_airtime_summary
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ConfirmationStep(AirtimeStep):
    """Generates confirmation snapshot."""

    async def execute(
        self,
        data: AirtimePayload,
        context: AirtimeContext,
        gates: AirtimeGates,
        worker_context: Any,
    ) -> TransactionResult:
        snapshot = {
            "amount": data.amount,
            "recipient_phone": data.recipient_phone,
            "network": data.network,
            "source_account": data.source_account_number,
        }

        if gates.confirmation_confirmed:
            return TransactionResult(outcome=TransactionOutcome.OK)

        summary = format_airtime_summary(
            {
                "amount": data.amount,
                "recipientPhone": data.recipient_phone,
                "network": data.network,
                "recipientName": data.recipient_name,
                "sourceBank": data.source_bank_name,
                "sourceAccount": data.source_account_number,
            }
        )

        try:
            if not worker_context.queue._redis:
                await worker_context.queue.connect()

            key = data.idempotency_key

            # Persist tokens so Webhook can look them up
            await worker_context.queue._redis.setex(
                f"airtime:token:{key}:phone",
                3600,
                context.phone_number,
            )
            await worker_context.queue._redis.setex(
                f"transaction:token:{key}:phone",
                3600,
                context.phone_number,
            )
        except Exception as e:
            logger.error("failed_to_persist_airtime_token", error=str(e))

        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            confirmation_summary=summary,
            confirmation_snapshot=snapshot,
        )
