from typing import Any

from apps.core.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.core.src.agent.graphs.data.pipeline.base import PipelineStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult


class SourceSelectionStep(PipelineStep):
    """Selection Step: Select source account."""

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult | None:
        if not context.accounts:
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error="No accounts available.",
            )

        return None
