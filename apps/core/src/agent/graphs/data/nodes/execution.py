from typing import Any

from apps.core.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.core.src.agent.graphs.data.pipeline.base import PipelineStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.i18n import render_message


class ExecutionStep(PipelineStep):
    """Execution Step: Execute the purchase."""

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult | None:
        locale = context.language
        payload.transaction_id = "txn-123"
        payload.receipt = {"id": "txn-123", "amount": payload.amount, "status": "success"}

        if payload.plan_name:
            payload.receipt["description"] = render_message(
                "data.execution.receipt_description_plan",
                locale,
                {"plan_name": payload.plan_name, "target_phone": payload.target_phone or ""},
            )
        else:
            payload.receipt["description"] = render_message(
                "data.execution.receipt_description_network",
                locale,
                {"network": payload.network or "", "target_phone": payload.target_phone or ""},
            )

        return TransactionResult(
            outcome=TransactionOutcome.OK,
            receipt=payload.receipt,
            patch=payload.model_dump(exclude_none=True),
        )
