from typing import Any

from apps.chat.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.chat.src.agent.graphs.data.pipeline.base import PipelineStep
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult


class AuthorizationStep(PipelineStep):
    """Authorization Step: Check PIN."""

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult | None:
        del context, worker_context
        if gates.pin_verified:
            return None

        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_AUTH,
            patch=payload.model_dump(exclude_none=True),
        )
