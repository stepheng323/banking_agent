from unittest.mock import AsyncMock

import pytest

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.executors.support import SupportTaskExecutor
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices
from banking.runtime.results import (
    SupportOutcome,
    SupportResult,
    TransactionOutcome,
    TransactionResult,
)


class _SupportWorkerStub:
    def __init__(self) -> None:
        self.last_payload: dict | None = None
        self.result = SupportResult(outcome=SupportOutcome.OK, response="ok")

    async def run(self, payload: dict, context: dict, user_message: str | None = None) -> SupportResult:
        del context, user_message
        self.last_payload = payload
        return self.result


class _TransferWorkerStub:
    def __init__(self) -> None:
        self.last_payload: dict | None = None
        self.last_user_message: str | None = None

    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del context, pin_verified
        self.last_payload = payload
        self.last_user_message = user_message
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["recipient_name"],
            prompt="Which transfer should I resend?",
        )


@pytest.mark.asyncio
async def test_support_handler_passes_quoted_message_id_without_mutating_task_payload() -> None:
    worker = _SupportWorkerStub()
    task = TaskSpec(
        id="t1",
        type="support",
        stage=TaskStage.DRAFT,
        payload={"intent": "receipt_request"},
    )
    state = OrchestratorState(
        user_id="u1",
        phone_number="2348000000001",
        channel="whatsapp",
        quoted_message_id="wamid.receipt.1",
        last_message_text="this transfer failed",
        tasks={"t1": task},
        waves=[["t1"]],
        loaded_context={"language": "en", "user_id": "u1", "profile": {"email": "u1@example.com"}},
    )

    ctx = ExecutionTurnContext(
        state=state,
        config={"configurable": {}},
        services=OrchestrationServices.from_mapping({"support": worker}),
        current_wave_len=1,
        accumulator=ExecutionAccumulator(state.tasks),
    )

    await SupportTaskExecutor().execute(task, "t1", ctx)

    assert worker.last_payload is not None
    assert worker.last_payload["quoted_message_id"] == "wamid.receipt.1"
    assert "quoted_message_id" not in task.payload
    assert task.stage == TaskStage.COMPLETED


@pytest.mark.asyncio
async def test_support_handler_enqueues_receipt_jobs() -> None:
    worker = _SupportWorkerStub()
    worker.result = SupportResult(
        outcome=SupportOutcome.OK,
        response="sending",
        receipt_jobs=[{"transaction_reference": "tx-2", "phone_number": "2348000000001"}],
    )
    publisher = AsyncMock()
    task = TaskSpec(
        id="t1",
        type="support",
        stage=TaskStage.DRAFT,
        payload={"intent": "receipt_request"},
    )
    state = OrchestratorState(
        user_id="u1",
        phone_number="2348000000001",
        channel="whatsapp",
        last_message_text="receipt for the second transaction",
        tasks={"t1": task},
        waves=[["t1"]],
        loaded_context={"language": "en", "user_id": "u1", "profile": {"email": "u1@example.com"}},
    )

    ctx = ExecutionTurnContext(
        state=state,
        config={"configurable": {"publisher": publisher}},
        services=OrchestrationServices.from_mapping({"support": worker}),
        current_wave_len=1,
        accumulator=ExecutionAccumulator(state.tasks),
    )

    await SupportTaskExecutor().execute(task, "t1", ctx)

    publisher.publish.assert_awaited_once_with(
        "receipt.process", {"transaction_reference": "tx-2", "phone_number": "2348000000001"}
    )
    assert ctx.accumulator.updates["outbox"] == [{"type": "say", "text": "sending"}]


@pytest.mark.asyncio
async def test_support_handler_reroutes_replay_modifier_to_transfer() -> None:
    support_worker = _SupportWorkerStub()
    transfer_worker = _TransferWorkerStub()
    task = TaskSpec(
        id="t1",
        type="support",
        stage=TaskStage.DRAFT,
        payload={"intent": "handle_request"},
    )
    state = OrchestratorState(
        user_id="u1",
        phone_number="2348000000001",
        channel="whatsapp",
        last_message_text="Again, but from gtb",
        tasks={"t1": task},
        waves=[["t1"]],
        loaded_context={"language": "en", "user_id": "u1", "profile": {"email": "u1@example.com"}},
    )

    ctx = ExecutionTurnContext(
        state=state,
        config={"configurable": {}},
        services=OrchestrationServices.from_mapping({"support": support_worker, "transfer": transfer_worker}),
        current_wave_len=1,
        accumulator=ExecutionAccumulator(state.tasks),
    )

    await SupportTaskExecutor().execute(task, "t1", ctx)

    assert support_worker.last_payload is None
    assert task.type == "transfer"
    assert transfer_worker.last_payload == {
        "message": "Again, but from gtb",
        "instruction": "Again, but from gtb",
    }
    assert transfer_worker.last_user_message == "Again, but from gtb"
    assert ctx.accumulator.prompts == ["Which transfer should I resend?"]
