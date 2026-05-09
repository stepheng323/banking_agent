from typing import Any

from apps.chat.src.agent.graphs.transfer.models.types import (
    TransferConfirmation,
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.chat.src.agent.graphs.transfer.pipeline.base import TransferPipeline, TransferStep
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult


class _PatchRecipientAccountStep(TransferStep):
    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any = None,
    ) -> TransactionResult:
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={"recipient_account": "08162511023"},
        )


async def test_pipeline_normalizes_recipient_account_patch_centrally() -> None:
    pipeline = TransferPipeline(steps=[_PatchRecipientAccountStep()])
    payload = TransferPayload()
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    gates = TransferGates()

    result = await pipeline.run(payload, context, gates, worker_context=None)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["recipient_account"] == "8162511023"


def test_pipeline_normalizes_confirmation_patch_centrally() -> None:
    normalized_patch = TransferPipeline._normalize_patch({"confirmation": {"confirmed": False}})

    assert isinstance(normalized_patch["confirmation"], TransferConfirmation)
    assert normalized_patch["confirmation"].confirmed is False
