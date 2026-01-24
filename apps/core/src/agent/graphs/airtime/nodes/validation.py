"""Airtime validation."""

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


class ValidationStep(AirtimeStep):
    """Validates airtime data."""

    async def execute(
        self,
        data: AirtimePayload,
        context: AirtimeContext,
        gates: AirtimeGates,
        worker_context: Any,
    ) -> TransactionResult:
        missing = []
        logger.info("Validating airtime data", data=data)

        if not data.recipient_phone:
            missing.append("phone number")

        if not data.amount:
            missing.append("amount")

        if missing:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["amount", "recipient_phone"] if len(missing) > 1 else [missing[0].replace(" ", "_")],
                prompt=f"Please provide the {' and '.join(missing)}.",
            )

        if data.amount <= 0:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["amount"],
                prompt="The amount must be greater than zero.",
            )

        if data.amount > 50000:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["amount"],
                prompt="The maximum airtime purchase is ₦50,000. Please enter a lower amount.",
            )

        if not data.network:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["recipient_phone"],
                prompt=f"I couldn't identify the network for {data.recipient_phone}. Please check the number.",
            )

        return TransactionResult(outcome=TransactionOutcome.OK)
