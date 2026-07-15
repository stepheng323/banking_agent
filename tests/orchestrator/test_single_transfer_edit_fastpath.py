import pytest

from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.node import handle_pending_interrupt
from banking.runtime.results import TransactionOutcome, TransactionResult
from shared.observability.llm import LLMCallDeadlineExceeded
from shared.types.planner import PendingActionEditDecision


class _TransferEditWorker:
    def __init__(self, result: TransactionResult) -> None:
        self.result = result
        self.calls = 0

    async def interpret_pending_confirmation_edit(self, **_: object) -> TransactionResult:
        self.calls += 1
        return self.result


class _TimedOutTransferEditWorker:
    calls = 0

    async def interpret_pending_confirmation_edit(self, **_: object) -> TransactionResult:
        self.calls += 1
        raise LLMCallDeadlineExceeded(role="transfer_extractor", deadline_seconds=15)


class _PendingEditPlanner:
    def __init__(self) -> None:
        self.calls = 0

    async def interpret_pending_action_edit(self, *_: object, **__: object) -> PendingActionEditDecision:
        self.calls += 1
        return PendingActionEditDecision(
            operation="update_fields",
            confidence=0.95,
            target_types=["transfer"],
            amount_mutation={"steps": [{"operation": "set", "amount": 20000}]},
        )


def _state() -> OrchestratorState:
    return OrchestratorState(
        user_id="u_single_transfer_edit",
        phone_number="2348162511023",
        channel="telegram",
        last_message_text="Add 5k",
        pending_interrupt=PendingInterrupt(kind="confirmation", task_ids=["transfer_1"]),
        waves=[["transfer_1"]],
        current_wave_index=0,
        loaded_context={"language": "en"},
        tasks={
            "transfer_1": TaskSpec(
                id="transfer_1",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 10000,
                    "recipient_name": "Adebayo",
                    "confirmation": {"summary": "Review", "snapshot": {"amount": 10000}},
                },
            )
        },
    )


@pytest.mark.asyncio
async def test_single_transfer_edit_fast_path_avoids_pending_action_llm() -> None:
    worker = _TransferEditWorker(
        TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "amount": 15000,
                "confirmation": {"confirmed": False},
                "funding_plan": None,
                "suggested_funding_plan": None,
            },
        )
    )
    planner = _PendingEditPlanner()

    updates = await handle_pending_interrupt(
        _state(),
        {
            "configurable": {
                "services": {"transfer": worker},
                "task_planner": planner,
            }
        },
    )

    assert worker.calls == 1
    assert planner.calls == 0
    assert updates["pending_interrupt"] is None
    task = updates["tasks"]["transfer_1"]
    assert task.stage == TaskStage.EXTRACTED
    assert task.payload["amount"] == 15000
    assert task.payload["skip_extraction"] is True


@pytest.mark.asyncio
async def test_single_transfer_edit_no_match_falls_back_to_pending_action_llm() -> None:
    worker = _TransferEditWorker(TransactionResult(outcome=TransactionOutcome.OK))
    planner = _PendingEditPlanner()

    updates = await handle_pending_interrupt(
        _state(),
        {
            "configurable": {
                "services": {"transfer": worker},
                "task_planner": planner,
            }
        },
    )

    assert worker.calls == 1
    assert planner.calls == 1
    assert updates["tasks"]["transfer_1"].payload["amount"] == 20000


@pytest.mark.asyncio
async def test_batch_confirmation_does_not_attempt_single_transfer_edit_fast_path() -> None:
    state = _state()
    state.pending_interrupt = PendingInterrupt(kind="confirmation", task_ids=["transfer_1", "transfer_2"])
    state.tasks["transfer_2"] = TaskSpec(
        id="transfer_2",
        type="transfer",
        stage=TaskStage.AWAITING_CONFIRMATION,
        payload={"amount": 5000, "recipient_name": "Mum", "confirmation": {"summary": "Review"}},
    )
    worker = _TransferEditWorker(TransactionResult(outcome=TransactionOutcome.OK, patch={"amount": 15000}))
    planner = _PendingEditPlanner()

    await handle_pending_interrupt(
        state,
        {
            "configurable": {
                "services": {"transfer": worker},
                "task_planner": planner,
            }
        },
    )

    assert worker.calls == 0
    assert planner.calls == 1


@pytest.mark.asyncio
async def test_scheduling_edit_enters_broad_path_without_wasting_amendment_call() -> None:
    state = _state()
    state.last_message_text = "Actually use First Bank and make it tomorrow morning"
    worker = _TransferEditWorker(TransactionResult(outcome=TransactionOutcome.OK, patch={"amount": 15000}))
    planner = _PendingEditPlanner()

    await handle_pending_interrupt(
        state,
        {
            "configurable": {
                "services": {"transfer": worker},
                "task_planner": planner,
            }
        },
    )

    assert worker.calls == 0
    assert planner.calls == 1


@pytest.mark.asyncio
async def test_single_transfer_edit_timeout_preserves_pending_state_without_router_fallback() -> None:
    worker = _TimedOutTransferEditWorker()
    planner = _PendingEditPlanner()

    updates = await handle_pending_interrupt(
        _state(),
        {"configurable": {"services": {"transfer": worker}, "task_planner": planner}},
    )

    assert worker.calls == 1
    assert planner.calls == 0
    assert updates["pending_interrupt"].task_ids == ["transfer_1"]
    assert updates["tasks"]["transfer_1"].stage == TaskStage.AWAITING_CONFIRMATION
    assert "unchanged" in updates["outbox"][0]["text"]
