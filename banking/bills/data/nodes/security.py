from typing import Any

from banking.bills.data.models.types import DataContext, DataGates, DataPayload
from banking.bills.data.pipeline.base import PipelineStep, continue_pipeline
from banking.runtime.results import TransactionOutcome, TransactionResult


class AuthorizationStep(PipelineStep):
    """Authorization Step: Check PIN."""

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult:
        del context, worker_context
        if gates.pin_verified:
            return continue_pipeline(payload)

        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_AUTH,
            patch=payload.model_dump(exclude_none=True),
        )
