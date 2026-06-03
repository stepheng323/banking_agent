from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from banking.bills.data.models.types import DataContext, DataGates, DataPayload
from banking.bills.data.pipeline.base import PipelineStep, continue_pipeline
from banking.presentation.i18n.renderer import render_message


class ValidationStep(PipelineStep):
    """Validation Step: Check balance and limits."""

    async def run(
        self, payload: DataPayload, context: DataContext, _gates: DataGates, _worker_context: Any
    ) -> TransactionResult:
        locale = context.language
        missing = []
        if not payload.plan_code:
            missing.append("plan_code")
        if not payload.plan_name:
            missing.append("plan_name")
        if payload.amount is None:
            missing.append("amount")
        if not payload.target_phone:
            missing.append("target_phone")
        if not payload.network:
            missing.append("network")
        if not payload.source_account_id:
            missing.append("source_account_id")

        if missing:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["data_plan_id"] if "plan_code" in missing else missing,
                prompt=render_message("data.plan_selection.missing_plan", locale),
                patch=payload.model_dump(exclude_none=True),
            )

        return continue_pipeline(payload)
