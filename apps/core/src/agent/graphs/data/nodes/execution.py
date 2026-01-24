from typing import Any

from apps.core.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.core.src.agent.graphs.data.pipeline.base import PipelineStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult


class ExecutionStep(PipelineStep):
    """Execution Step: Execute the purchase."""

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult | None:
        payload.transaction_id = "txn-123"
        payload.receipt = {"id": "txn-123", "amount": payload.amount, "status": "success"}

        if payload.plan_name:
            payload.receipt["description"] = f"{payload.plan_name} for {payload.target_phone}"
        else:
            payload.receipt["description"] = f"{payload.network} data for {payload.target_phone}"

        return TransactionResult(
            outcome=TransactionOutcome.OK,
            receipt=payload.receipt,
            patch=payload.model_dump(exclude_none=True),
        )
