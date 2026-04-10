"""Airtime security step."""

from typing import Any

from apps.core.src.agent.graphs.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from apps.core.src.agent.graphs.airtime.pipeline.base import AirtimeStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult


class AuthorizationStep(AirtimeStep):
    """Enforces authorization."""

    async def execute(
        self,
        data: AirtimePayload,
        context: AirtimeContext,
        gates: AirtimeGates,
        worker_context: Any,
    ) -> TransactionResult:
        del data, context, worker_context
        if gates.pin_verified:
            return TransactionResult(outcome=TransactionOutcome.OK)

        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_AUTH,
        )
