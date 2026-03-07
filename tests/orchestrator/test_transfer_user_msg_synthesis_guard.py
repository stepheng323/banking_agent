from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.execution.handlers import (
    ExecutionAggregation,
    ExecutionContext,
    handle_transfer_task,
)
from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage, TransactionOutcome, TransactionResult
from apps.core.src.agent.orchestrator.models.state import OrchestratorState


class _CaptureTransferWorker:
    def __init__(self) -> None:
        self.last_user_message: str | None = None

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del payload, context, pin_verified
        self.last_user_message = user_message
        return TransactionResult(outcome=TransactionOutcome.OK, patch={})


async def _run_transfer_with_message(last_message_text: str | None) -> str | None:
    worker = _CaptureTransferWorker()
    task = TaskSpec(
        id="t1",
        type="transfer",
        stage=TaskStage.EXTRACTED,
        payload={
            "recipient_name": "Mum",
            "amount": 5000,
            "source_account_id": "acc_1",
        },
    )
    state = OrchestratorState(
        user_id="u_transfer_guard",
        phone_number="2348000000123",
        channel="whatsapp",
        last_message_text=last_message_text,
        loaded_context={"language": "en", "user_id": "u_transfer_guard", "accounts": [], "beneficiaries": []},
        tasks={"t1": task},
        waves=[["t1"]],
        current_wave_index=0,
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}
    ctx = ExecutionContext(
        state=state,
        config=config,
        services={"transfer": worker},
        current_wave_len=1,
        agg=ExecutionAggregation(state.tasks),
    )
    await handle_transfer_task(task, "t1", ctx)
    return worker.last_user_message


@pytest.mark.asyncio
async def test_transfer_handler_preserves_explicit_update_message() -> None:
    user_message = await _run_transfer_with_message("Change to 25k")
    assert user_message == "Change to 25k"


@pytest.mark.asyncio
async def test_transfer_handler_synthesizes_when_message_missing() -> None:
    user_message = await _run_transfer_with_message(None)
    assert user_message == "Send 5000 to Mum"


@pytest.mark.asyncio
async def test_transfer_handler_synthesizes_when_message_is_whitespace_only() -> None:
    user_message = await _run_transfer_with_message("   ")
    assert user_message == "Send 5000 to Mum"
