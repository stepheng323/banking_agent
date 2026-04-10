from typing import Any

from apps.core.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.core.src.agent.graphs.data.pipeline.base import PipelineStep
from apps.core.src.agent.orchestrator.models.domain import TransactionResult


class ValidationStep(PipelineStep):
    """Validation Step: Check balance and limits."""

    async def run(
        self, payload: DataPayload, _context: DataContext, _gates: DataGates, _worker_context: Any
    ) -> TransactionResult | None:
        if not payload.amount:
            return None

        return None
