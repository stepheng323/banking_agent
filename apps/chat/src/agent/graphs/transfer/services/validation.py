"""Validation service for transfer operations."""

from apps.chat.src.agent.graphs.__shared__.validation.amount_validator import (
    TRANSFER_LIMITS,
    validate_percentage,
)
from apps.chat.src.agent.graphs.__shared__.validation.amount_validator import (
    validate_amount as validate_amount_limits,
)
from apps.chat.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferPayload,
)
from apps.chat.src.agent.graphs.transfer.validators.self_transfer_validator import (
    SelfTransferValidator,
)
from apps.chat.src.agent.orchestrator.models.domain import (
    TransactionOutcome,
    TransactionResult,
)
from shared.i18n import render_message


class ValidationService:
    """Service to handle business rule validations for transfers."""

    def validate_amount(self, payload: TransferPayload, ctx: TransferContext) -> TransactionResult:
        """Validate amount limits and percentages."""
        locale = ctx.language
        if payload.transfer_percentage:
            is_valid, error, pct = validate_percentage(payload.transfer_percentage, locale=locale)
            if not is_valid:
                return TransactionResult(outcome=TransactionOutcome.FAILED, error=error)
            return TransactionResult(outcome=TransactionOutcome.OK, patch={"transfer_percentage": pct})

        if payload.transfer_all:
            return TransactionResult(outcome=TransactionOutcome.OK)

        if not payload.amount:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["amount"],
                prompt=render_message("transfer.validation.ask_amount", locale),
            )

        is_valid, error, amount = validate_amount_limits(payload.amount, TRANSFER_LIMITS, locale=locale)
        if not is_valid:
            return TransactionResult(outcome=TransactionOutcome.FAILED, error=error)

        if amount != payload.amount:
            return TransactionResult(outcome=TransactionOutcome.OK, patch={"amount": amount})

        return TransactionResult(outcome=TransactionOutcome.OK)

    def validate_transfer(self, payload: TransferPayload, ctx: TransferContext) -> TransactionResult:
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
                locale=ctx.language,
            )
            if not is_valid:
                return TransactionResult(outcome=TransactionOutcome.FAILED, error=error)

        return TransactionResult(outcome=TransactionOutcome.OK)
