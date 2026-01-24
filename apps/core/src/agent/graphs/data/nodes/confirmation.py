from typing import Any

from apps.core.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.core.src.agent.graphs.data.pipeline.base import PipelineStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult


class ConfirmationStep(PipelineStep):
    """Confirmation Step: Generate confirmation snapshot."""

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult | None:
        if gates.confirmation_confirmed:
            return None

        summary = f"Buy {payload.network} data for {payload.target_phone}?"
        if payload.plan_name:
            summary = f"Buy {payload.plan_name} for {payload.target_phone}?"
        elif payload.amount:
            summary = f"Buy N{payload.amount} of {payload.network} data for {payload.target_phone}?"

        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            confirmation_summary=summary,
            confirmation_snapshot=payload.model_dump(mode="json"),
            patch=payload.model_dump(exclude_none=True),
        )
