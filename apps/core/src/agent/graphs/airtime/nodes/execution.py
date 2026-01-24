"""Airtime execution step."""

from typing import Any

from apps.core.src.agent.graphs.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from apps.core.src.agent.graphs.airtime.pipeline.base import AirtimeStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ExecutionStep(AirtimeStep):
    """Executes the airtime purchase."""

    async def execute(
        self,
        data: AirtimePayload,
        context: AirtimeContext,
        gates: AirtimeGates,
        worker_context: Any,
    ) -> TransactionResult:
        if not walker_context.banking_provider:
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error="Banking provider not available",
            )

        try:
            request_payload = {
                "amount": data.amount,
                "recipient": {
                    "phone_number": data.recipient_phone,
                    "network": data.network,
                },
                "source": {
                    "account_number": data.source_account_number,
                    "account_id": data.source_account_id,
                },
                "idempotency_key": data.idempotency_key,
            }

            result = await worker_context.banking_provider.purchase_airtime(request_payload)

            if not result.get("success"):
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=result.get("error") or "Airtime purchase failed",
                )

            return TransactionResult(
                outcome=TransactionOutcome.OK,
                receipt={
                    "transaction_id": result.get("transaction_id"),
                    "amount": data.amount,
                    "recipient_phone": data.recipient_phone,
                    "network": data.network,
                    "status": "success",
                },
            )

        except Exception as e:
            logger.error("airtime_execution_error", error=str(e), exc_info=True)
            return TransactionResult(
                outcome=TransactionOutcome.FAILED, error="System error during execution.", retryable=True
            )
