from typing import Any

from apps.core.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.core.src.agent.graphs.data.pipeline.base import PipelineStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult


class AuthorizationStep(PipelineStep):
    """Authorization Step: Check PIN."""

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult | None:
        if gates.pin_verified:
            return None

        # Determine summary for auth screen (same as confirmation)
        summary = f"Buy {payload.network} data for {payload.target_phone}?"
        if payload.plan_name:
            summary = f"Buy {payload.plan_name} for {payload.target_phone}?"

        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_AUTH,
            patch=payload.model_dump(exclude_none=True),
        )
