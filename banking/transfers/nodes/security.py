"""Security/Auth logic."""

from typing import Any

from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transfers.authorization.pin_token import persist_transfer_pin_token
from banking.transfers.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from banking.transfers.pipeline.base import TransferStep


class AuthorizationStep(TransferStep):
    """Checks for authorization (PIN)."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any = None,
    ) -> TransactionResult:
        if not gates.confirmation_confirmed:
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})

        if gates.pin_verified:
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})

        await persist_transfer_pin_token(
            idempotency_key=data.idempotency_key,
            phone_number=context.phone_number,
            worker_context=worker_context,
        )
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_AUTH,
            patch=data.model_dump(exclude_none=True),
        )
