from types import SimpleNamespace

import pytest

from apps.core.src.agent.orchestrator.execution.handlers import (
    ExecutionAggregation,
    ExecutionContext,
    handle_support_task,
)
from apps.core.src.agent.orchestrator.models.domain import SupportOutcome, SupportResult, TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState


class _SupportWorkerStub:
    def __init__(self) -> None:
        self.last_payload: dict | None = None

    async def run(self, payload: dict, context: dict, user_message: str | None = None) -> SupportResult:
        del context, user_message
        self.last_payload = payload
        return SupportResult(outcome=SupportOutcome.OK, response="ok")


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

    ctx = ExecutionContext(
        state=state,
        config={"configurable": {}},
        services={"support": worker},
        current_wave_len=1,
        agg=ExecutionAggregation(state.tasks),
    )

    await handle_support_task(task, "t1", ctx)

    assert worker.last_payload is not None
    assert worker.last_payload["quoted_message_id"] == "wamid.receipt.1"
    assert "quoted_message_id" not in task.payload
    assert task.stage == TaskStage.COMPLETED
