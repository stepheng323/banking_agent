"""Security/Auth logic."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.chat.src.agent.workers.transfer.authorization.pin_token import persist_transfer_pin_token
from apps.chat.src.agent.workers.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.chat.src.agent.workers.transfer.pipeline.base import TransferStep


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
