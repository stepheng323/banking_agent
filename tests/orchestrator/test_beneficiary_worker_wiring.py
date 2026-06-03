from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage, TransactionOutcome, TransactionResult
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.task_handlers.account_beneficiary import handle_beneficiary_task
from apps.chat.src.agent.orchestrator.task_handlers.runtime import ExecutionAggregation, ExecutionContext


class _InjectedBeneficiaryWorker:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, *, payload: dict[str, Any], context: dict[str, Any]) -> TransactionResult:
        self.calls.append({"payload": payload.copy(), "context": context.copy()})
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response="Saved beneficiaries",
            details={"viewed_beneficiaries": [{"name": "Tolu", "alias": "Tolu"}]},
        )


async def test_beneficiary_management_uses_injected_service() -> None:
    task = TaskSpec(
        id="beneficiary_1",
        type="beneficiary",
        stage=TaskStage.DRAFT,
        payload={"action": "list_beneficiaries"},
    )
    state = OrchestratorState(
        user_id="user-1",
        phone_number="2348000000001",
        loaded_context={"user_id": "user-1", "language": "en"},
        tasks={"beneficiary_1": task},
    )
    worker = _InjectedBeneficiaryWorker()
    ctx = ExecutionContext(
        state=state,
        config={"configurable": {}},
        services={"beneficiary": worker},
        current_wave_len=1,
        agg=ExecutionAggregation(state.tasks),
    )

    await handle_beneficiary_task(task, "beneficiary_1", ctx)

    assert len(worker.calls) == 1
    assert worker.calls[0]["payload"]["intent"] == "list_beneficiaries"
    assert worker.calls[0]["context"]["user_id"] == "user-1"
    assert worker.calls[0]["context"]["phone_number"] == "2348000000001"
    assert task.stage == TaskStage.COMPLETED
    assert task.payload["result"] == "Saved beneficiaries"
    assert ctx.agg.updates["outbox"] == [{"type": "say", "text": "Saved beneficiaries"}]
