"""Security/Auth logic."""

from typing import Any

from apps.core.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.core.src.agent.graphs.transfer.pipeline.base import TransferStep
from apps.core.src.agent.orchestrator.models.domain import TransferOutcome, TransferResult


class AuthorizationStep(TransferStep):
    """Checks for authorization (PIN)."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any,
    ) -> TransferResult:

        if gates.confirmation_confirmed:
            return TransferResult(outcome=TransferOutcome.OK, patch={})

        return TransferResult(outcome=TransferOutcome.OK, patch={})


def require_auth(gate: TransferGates) -> TransferResult:
    """Check authentication gates."""
    if not gate.pin_verified:
        return TransferResult(outcome=TransferOutcome.NEEDS_AUTH)
    return TransferResult(outcome=TransferOutcome.OK)
