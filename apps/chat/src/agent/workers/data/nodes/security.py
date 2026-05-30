from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.chat.src.agent.workers.data.models.types import DataContext, DataGates, DataPayload
from apps.chat.src.agent.workers.data.pipeline.base import PipelineStep, continue_pipeline


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
