from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextFrameType
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.executors.transfer import ScheduleTaskExecutor
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices
from banking.runtime.results import TransactionOutcome, TransactionResult


class _InjectedScheduleWorker:
    def __init__(self, *, response: str, schedule_response_mode: str | None = None) -> None:
        self.response = response
        self.schedule_response_mode = schedule_response_mode
        self.calls: list[dict[str, Any]] = []

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del user_message, pin_verified
        self.calls.append({"payload": payload.copy(), "context": context.copy()})
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response=self.response,
            patch={
                "is_scheduled_operation": True,
                "skip_finalize_summary": True,
                "schedule_context_items": [
                    {
                        "entity_id": "sch-transfer",
                        "label": "Transfer: ₦5,000 Mum • One Time at 8:00 AM WAT",
                        "data": {
                            "type": "scheduled_transaction",
                            "schedule_id": "sch-transfer",
                            "domain": "Transfer",
                            "domain_key": "transfer",
                            "amount": "₦5,000",
                            "target": "Mum",
                            "recurrence": "One Time",
                            "schedule_time": "8:00 AM WAT",
                            "next_run": "Jun 12, 2026, 8:00 AM WAT",
                            "status": "active",
                            "summary": "Transfer: ₦5,000 Mum • One Time at 8:00 AM WAT",
                        },
                    }
                ],
            },
        )


def _context(task: TaskSpec, worker: _InjectedScheduleWorker) -> ExecutionTurnContext:
    state = OrchestratorState(
        user_id="user-1",
        phone_number="2348000000001",
        loaded_context={"user_id": "user-1", "language": "en"},
        tasks={task.id: task},
    )
    return ExecutionTurnContext(
        state=state,
        config={"configurable": {}},
        services=OrchestrationServices.from_mapping({"transfer": worker}),
        current_wave_len=1,
        accumulator=ExecutionAccumulator(state.tasks),
    )


async def test_schedule_executor_attaches_mobile_body_blocks_and_pushes_context_frame() -> None:
    task = TaskSpec(
        id="schedule_1",
        type="schedule",
        stage=TaskStage.DRAFT,
        payload={"action": "list_scheduled_transactions", "schedule_response_mode": "list"},
    )
    worker = _InjectedScheduleWorker(response="Scheduled transactions:\n1. Transfer: ₦5,000 Mum")
    ctx = _context(task, worker)

    await ScheduleTaskExecutor().execute(task, "schedule_1", ctx)

    assert len(worker.calls) == 1
    assert task.stage == TaskStage.COMPLETED
    updates = ctx.accumulator.to_updates()
    assert updates["outbox"][0] == {
        "type": "say",
        "text": "Scheduled transactions:\n1. Transfer: ₦5,000 Mum",
        "body_blocks": [
            {"type": "heading", "text": "Scheduled transactions"},
            {
                "type": "text",
                "text": "1. Transfer — ₦5,000 to Mum\nOne Time • 8:00 AM WAT\nNext: Jun 12, 2026, 8:00 AM WAT",
            },
        ],
    }
    assert updates["context_frames"][-1].frame_type == ContextFrameType.SCHEDULE_LIST
    assert updates["context_frames"][-1].items[0].entity_id == "sch-transfer"


async def test_schedule_executor_keeps_count_mode_plain_even_with_context_items() -> None:
    task = TaskSpec(
        id="schedule_count",
        type="schedule",
        stage=TaskStage.DRAFT,
        payload={"action": "list_scheduled_transactions", "schedule_response_mode": "count"},
    )
    worker = _InjectedScheduleWorker(response="Pending scheduled transactions: 1.", schedule_response_mode="count")
    ctx = _context(task, worker)

    await ScheduleTaskExecutor().execute(task, "schedule_count", ctx)

    assert ctx.accumulator.to_updates()["outbox"] == [{"type": "say", "text": "Pending scheduled transactions: 1."}]
