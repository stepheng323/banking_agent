from typing import Any

import pytest

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from banking.bills.data.models.types import DataContext, DataGates, DataPayload
from banking.bills.data.pipeline.base import DataPipeline, PipelineStep


class _PatchStep(PipelineStep):
    async def run(
        self,
        payload: DataPayload,
        context: DataContext,
        gates: DataGates,
        worker_context: Any,
    ) -> TransactionResult:
        del payload, context, gates, worker_context
        return TransactionResult(outcome=TransactionOutcome.OK, patch={"network": "MTN"})


class _AssertPatchedStep(PipelineStep):
    async def run(
        self,
        payload: DataPayload,
        context: DataContext,
        gates: DataGates,
        worker_context: Any,
    ) -> TransactionResult:
        del context, gates, worker_context
        assert payload.network == "MTN"
        return TransactionResult(outcome=TransactionOutcome.OK, patch={"target_phone": "08162511023"})


class _NeedsAuthStep(PipelineStep):
    async def run(
        self,
        payload: DataPayload,
        context: DataContext,
        gates: DataGates,
        worker_context: Any,
    ) -> TransactionResult:
        del payload, context, gates, worker_context
        return TransactionResult(outcome=TransactionOutcome.NEEDS_AUTH, patch={"idempotency_key": "data-key"})


@pytest.mark.asyncio
async def test_data_pipeline_continues_on_ok_and_accumulates_patches() -> None:
    result = await DataPipeline([_PatchStep(), _AssertPatchedStep()]).run(
        DataPayload(),
        DataContext(phone_number="2348000000000"),
        DataGates(),
        worker_context=None,
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["network"] == "MTN"
    assert result.patch["target_phone"] == "08162511023"


@pytest.mark.asyncio
async def test_data_pipeline_halts_on_non_ok() -> None:
    result = await DataPipeline([_PatchStep(), _NeedsAuthStep(), _AssertPatchedStep()]).run(
        DataPayload(),
        DataContext(phone_number="2348000000000"),
        DataGates(),
        worker_context=None,
    )

    assert result.outcome == TransactionOutcome.NEEDS_AUTH
    assert result.patch["network"] == "MTN"
    assert result.patch["idempotency_key"] == "data-key"
