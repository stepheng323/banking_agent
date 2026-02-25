"""Validation logic for transfer flow."""

from typing import Any

from apps.core.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.core.src.agent.graphs.transfer.pipeline.base import TransferStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.i18n import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ValidationStep(TransferStep):
    """Validates amount and transfer details."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any,
    ) -> TransactionResult:
        if (
            data.amount is None
            and not data.transfer_all
            and (data.recipient_resolved_name or data.recipient_name)
            and getattr(worker_context, "transaction_repo", None) is not None
            and getattr(worker_context, "user_id", None)
        ):
            recipient_hint = (data.recipient_resolved_name or data.recipient_name or "").strip()
            if recipient_hint:
                try:
                    recent = await worker_context.transaction_repo.get_recent_successful_transfer_by_recipient(
                        str(worker_context.user_id),
                        recipient_hint,
                    )
                    if recent and recent.amount:
                        suggested_amount = float(recent.amount)
                        return TransactionResult(
                            outcome=TransactionOutcome.NEEDS_INPUT,
                            required_fields=["amount"],
                            prompt=render_message(
                                "transfer.validation.ask_amount_with_suggestion",
                                context.language,
                                {
                                    "amount": f"₦{suggested_amount:,.0f}",
                                    "recipient_name": recipient_hint,
                                },
                            ),
                            patch={"suggested_amount": suggested_amount},
                            details={
                                "option_context": "TRANSFER_AMOUNT_SUGGESTION",
                                "options": [
                                    {"id": "1", "title": f"Use ₦{suggested_amount:,.0f}"},
                                    {"id": "2", "title": "Enter a new amount"},
                                ],
                            },
                        )
                except Exception as exc:
                    logger.warning("suggested_amount_lookup_failed", error=str(exc))

        service = worker_context.validation_service

        res_amount = service.validate_amount(data, context)
        if res_amount.outcome != TransactionOutcome.OK:
            return res_amount

        patch = res_amount.patch or {}

        data_for_val = data.model_copy(update=res_amount.patch) if res_amount.patch else data

        res_transfer = service.validate_transfer(data_for_val, context)
        if res_transfer.outcome != TransactionOutcome.OK:
            return res_transfer

        final_patch = patch
        if res_transfer.patch:
            final_patch.update(res_transfer.patch)

        return TransactionResult(outcome=TransactionOutcome.OK, patch=final_patch)
