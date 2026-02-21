from typing import Any

from apps.core.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.core.src.agent.graphs.data.pipeline.base import PipelineStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.i18n import render_message


class ResolutionStep(PipelineStep):
    """Resolution Step: Resolve ambiguities in network, plan, or target."""

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult | None:
        locale = context.language
        # 1. Resolve Target Phone
        if not payload.target_phone:
            if payload.extraction and payload.extraction.entities.is_self:
                payload.target_phone = context.phone_number
                payload.is_self = True
            elif payload.extraction and payload.extraction.entities.recipient_phone:
                payload.target_phone = payload.extraction.entities.recipient_phone
            # Else check context/beneficiaries (simplified)

        if not payload.target_phone:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["target_phone"],
                prompt=render_message("data.resolve.ask_target_phone", locale),
                patch=payload.model_dump(exclude_none=True),
            )

        # 2. Resolve Network
        if not payload.network:
            # Try to lookup network (mock or real provider check)
            # For now, if missing, ask
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["network"],
                prompt=render_message("data.resolve.ask_network", locale, {"target_phone": payload.target_phone}),
                patch=payload.model_dump(exclude_none=True),
            )

        # 3. Resolve Plan (if extracted amount/size)
        # This implies we might need a PlanSelection step or handle it here.
        # Ideally, we present a list of plans if 'plan_code' is missing but 'amount' is present.

        # ...Logic to fetch plans and match...

        return None  # Continue
