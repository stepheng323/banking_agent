"""Transfer recipient resolution pipeline stage."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TransactionResult
from banking.transfers.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from banking.transfers.pipeline.base import TransferStep
from banking.transfers.resolution.resolver import resolve_beneficiary


class ResolutionStep(TransferStep):
    """Resolves beneficiary details."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any = None,
    ) -> TransactionResult:
        del gates
        return await resolve_beneficiary(
            data,
            context,
            worker_context.resolver_provider,
            worker_context.bank_cache,
        )
