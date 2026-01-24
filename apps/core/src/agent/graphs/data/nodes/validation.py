from typing import Any

from apps.core.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.core.src.agent.graphs.data.pipeline.base import PipelineStep
from apps.core.src.agent.orchestrator.models.domain import TransactionResult


class ValidationStep(PipelineStep):
    """Validation Step: Check balance and limits."""

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult | None:
        if not payload.amount:
            # Can't validate without amount
            return None

        # Mock validation
        # if payload.amount > available_balance: ...

        return None
