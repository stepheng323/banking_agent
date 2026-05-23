from typing import Any

from apps.chat.src.agent.graphs.__shared__.scheduling import format_schedule_confirmation_line
from apps.chat.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.chat.src.agent.graphs.data.pipeline.base import PipelineStep
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.formatters.data import format_data_summary
from shared.i18n import render_message


class ConfirmationStep(PipelineStep):
    """Confirmation Step: Generate confirmation snapshot."""

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult | None:
        del worker_context
        if gates.confirmation_confirmed:
            return None

        locale = context.language
        if payload.amount:
            summary = format_data_summary(
                {
                    "planName": payload.plan_name,
                    "amount": payload.amount,
                    "recipientPhone": payload.target_phone,
                    "network": payload.network,
                    "sourceBank": payload.source_bank_name,
                    "sourceAccount": payload.source_account_number,
                    "isSelf": payload.is_self,
                },
                locale=locale,
            )
            schedule_line = format_schedule_confirmation_line(payload, locale)
            if schedule_line:
                summary = f"{summary}\n\n{schedule_line}"
        else:
            summary = render_message(
                "data.confirmation.buy_network_for_phone",
                locale,
                {"network": payload.network or "", "target_phone": payload.target_phone or ""},
            )
            if payload.plan_name:
                summary = render_message(
                    "data.confirmation.buy_plan_for_phone",
                    locale,
                    {"plan_name": payload.plan_name, "target_phone": payload.target_phone or ""},
                )

        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            confirmation_summary=summary,
            confirmation_snapshot=payload.model_dump(mode="json"),
            patch=payload.model_dump(exclude_none=True),
        )
