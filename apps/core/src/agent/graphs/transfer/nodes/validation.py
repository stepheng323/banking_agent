"""Validation logic for transfer flow."""

from typing import Any

from apps.core.src.agent.graphs.__shared__.validation.amount_validator import (
    TRANSFER_LIMITS,
    validate_percentage,
)
from apps.core.src.agent.graphs.__shared__.validation.amount_validator import (
    validate_amount as validate_amount_limits,
)
from apps.core.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.core.src.agent.graphs.transfer.pipeline.base import TransferStep
from apps.core.src.agent.graphs.transfer.validators import SelfTransferValidator
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
        res_amount = validate_amount(data, context)
        if res_amount.outcome != TransactionOutcome.OK:
            return res_amount

        patch = res_amount.patch or {}

        data_for_val = data.model_copy(update=res_amount.patch) if res_amount.patch else data

        res_transfer = validate_transfer(data_for_val)
        if res_transfer.outcome != TransactionOutcome.OK:
            return res_transfer

        final_patch = patch
        if res_transfer.patch:
            final_patch.update(res_transfer.patch)

        return TransactionResult(outcome=TransactionOutcome.OK, patch=final_patch)


def validate_amount(payload: TransferPayload, ctx: TransferContext) -> TransactionResult:
    """Validate amount limits and percentages."""
    if payload.transfer_percentage:
        is_valid, error, pct = validate_percentage(payload.transfer_percentage)
        if not is_valid:
            return TransactionResult(outcome=TransactionOutcome.FAILED, error=error)
        return TransactionResult(outcome=TransactionOutcome.OK, patch={"transfer_percentage": pct})

    if payload.transfer_all:
        return TransactionResult(outcome=TransactionOutcome.OK)

    if not payload.amount:
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["amount"],
            prompt="How much would you like to send?",
        )

    is_valid, error, amount = validate_amount_limits(payload.amount, TRANSFER_LIMITS)
    if not is_valid:
        return TransactionResult(outcome=TransactionOutcome.FAILED, error=error)

    if amount != payload.amount:
        return TransactionResult(outcome=TransactionOutcome.OK, patch={"amount": amount})

    return TransactionResult(outcome=TransactionOutcome.OK)


def validate_transfer(payload: TransferPayload) -> TransactionResult:
    """Business rule validations (self-transfer, etc)."""
    if payload.source_account_number and payload.recipient_account:
        val = SelfTransferValidator()
        is_valid, error = val.validate(
            recipient_account=payload.recipient_account,
            recipient_bank_code=payload.recipient_bank_code,
            recipient_bank_name=payload.recipient_bank_name,
            source_account={
                "account_number": payload.source_account_number,
                "bank_name": payload.source_bank_name,
            },
        )
        if not is_valid:
            return TransactionResult(outcome=TransactionOutcome.FAILED, error=error)

    return TransactionResult(outcome=TransactionOutcome.OK)
