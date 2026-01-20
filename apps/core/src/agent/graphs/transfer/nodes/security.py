"""Security/Auth logic."""

from apps.core.src.agent.graphs.transfer.models.types import TransferGates
from apps.core.src.agent.orchestrator.models.domain import TransferOutcome, TransferResult


def require_auth(gate: TransferGates) -> TransferResult:
    """Check authentication gates."""
    if not gate.pin_verified:
        return TransferResult(outcome=TransferOutcome.NEEDS_AUTH)
    return TransferResult(outcome=TransferOutcome.OK)
