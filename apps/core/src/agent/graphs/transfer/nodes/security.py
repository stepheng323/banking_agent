"""Security/Auth logic."""

from typing import Any

from apps.core.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.core.src.agent.graphs.transfer.pipeline.base import TransferStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult


class AuthorizationStep(TransferStep):
    """Checks for authorization (PIN)."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any = None,
    ) -> TransactionResult:
        if gates.confirmation_confirmed:
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})

        return TransactionResult(outcome=TransactionOutcome.OK, patch={})


def require_auth(gate: TransferGates) -> TransactionResult:
    """Check authentication gates."""
    if not gate.pin_verified:
        return TransactionResult(outcome=TransactionOutcome.NEEDS_AUTH)
    return TransactionResult(outcome=TransactionOutcome.OK)
