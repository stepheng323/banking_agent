"""Validation logic for transfer flow."""

from typing import Any

from apps.core.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.core.src.agent.graphs.transfer.pipeline.base import TransferStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
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
        service = worker_context.validation_service

        res_amount = service.validate_amount(data, context)
        if res_amount.outcome != TransactionOutcome.OK:
            return res_amount

        patch = res_amount.patch or {}

        data_for_val = data.model_copy(update=res_amount.patch) if res_amount.patch else data

        res_transfer = service.validate_transfer(data_for_val)
        if res_transfer.outcome != TransactionOutcome.OK:
            return res_transfer

        final_patch = patch
        if res_transfer.patch:
            final_patch.update(res_transfer.patch)

        return TransactionResult(outcome=TransactionOutcome.OK, patch=final_patch)
