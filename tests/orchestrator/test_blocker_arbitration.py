from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.blocker_arbitration import choose_wave_blocker
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_finalize import finalize_execution_wave_updates
from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_setup import ExecutionWaveRuntime
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices


def _task(task_id: str, *, stage: TaskStage, task_type: str = "transfer", group_id: str | None = None) -> TaskSpec:
    payload = {"async_group_id": group_id} if group_id else {}
    return TaskSpec(id=task_id, type=task_type, stage=stage, payload=payload)


def _state(tasks: dict[str, TaskSpec], *, wave: list[str]) -> OrchestratorState:
    return OrchestratorState(
        user_id="user-1",
        phone_number="2348000000000",
        channel="telegram",
        waves=[wave],
        current_wave_index=0,
        tasks=tasks,
        loaded_context={"language": "en"},
    )


def _aggregation(state: OrchestratorState) -> ExecutionAccumulator:
    return ExecutionAccumulator(state.tasks)


def test_missing_input_suppresses_confirmation_and_auth() -> None:
    state = _state(
        {
            "input": _task("input", stage=TaskStage.EXTRACTED),
            "confirm": _task("confirm", stage=TaskStage.AWAITING_CONFIRMATION),
            "auth": _task("auth", stage=TaskStage.AWAITING_AUTH),
        },
        wave=["input", "confirm", "auth"],
    )
    agg = _aggregation(state)
    agg.add_missing_fields("input", ["amount"])
    agg.add_confirmation_task("confirm")
    agg.add_auth_task("auth")

    decision = choose_wave_blocker(state=state, current_wave=state.waves[0], agg=agg)

    assert decision.kind == "input"
    assert decision.task_ids == ["input"]
    assert decision.suppressed_counts == {"confirmation": 1, "auth": 1}


def test_confirmation_suppresses_auth() -> None:
    state = _state(
        {
            "confirm": _task("confirm", stage=TaskStage.AWAITING_CONFIRMATION),
            "auth": _task("auth", stage=TaskStage.AWAITING_AUTH),
        },
        wave=["confirm", "auth"],
    )
    agg = _aggregation(state)
    agg.add_confirmation_task("confirm")
    agg.add_auth_task("auth")

    decision = choose_wave_blocker(state=state, current_wave=state.waves[0], agg=agg)

    assert decision.kind == "confirmation"
    assert decision.task_ids == ["confirm"]
    assert decision.suppressed_counts == {"auth": 1}


def test_auth_is_selected_when_no_higher_priority_blocker_exists() -> None:
    state = _state(
        {"auth": _task("auth", stage=TaskStage.AWAITING_AUTH)},
        wave=["auth"],
    )
    agg = _aggregation(state)
    agg.add_auth_task("auth")

    decision = choose_wave_blocker(state=state, current_wave=state.waves[0], agg=agg)

    assert decision.kind == "auth"
    assert decision.task_ids == ["auth"]
    assert decision.suppressed_counts == {}


def test_grouped_confirmation_expands_by_async_group_and_preserves_wave_order() -> None:
    state = _state(
        {
            "first": _task("first", stage=TaskStage.AWAITING_CONFIRMATION, group_id="group-1"),
            "middle": _task("middle", stage=TaskStage.COMPLETED),
            "last": _task("last", stage=TaskStage.AWAITING_CONFIRMATION, group_id="group-1"),
        },
        wave=["first", "middle", "last"],
    )
    agg = _aggregation(state)
    agg.add_confirmation_task("last")

    decision = choose_wave_blocker(state=state, current_wave=state.waves[0], agg=agg)

    assert decision.kind == "confirmation"
    assert decision.task_ids == ["first", "last"]


async def test_no_blocker_allows_terminal_wave_to_advance() -> None:
    state = _state(
        {"done": _task("done", stage=TaskStage.COMPLETED)},
        wave=["done"],
    )
    config: RunnableConfig = {"configurable": {}}
    agg = _aggregation(state)
    ctx = ExecutionTurnContext(
        state=state,
        config=config,
        services=OrchestrationServices.empty(),
        current_wave_len=1,
        accumulator=agg,
        current_wave_task_ids=["done"],
    )
    runtime = ExecutionWaveRuntime(
        current_wave=["done"],
        services=OrchestrationServices.empty(),
        accumulator=agg,
        ctx=ctx,
        task_executors={},
        locale="en",
        mandate_gate_accounts=[],
    )

    updates = await finalize_execution_wave_updates(state=state, runtime=runtime)

    assert updates["current_wave_index"] == 1


async def test_finalize_never_emits_confirmation_alongside_unresolved_input() -> None:
    state = _state(
        {
            "input": TaskSpec(
                id="input",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"amount": 2_000, "recipient_name": "Tolu Adebayo"},
            ),
            "ready": _task("ready", stage=TaskStage.AWAITING_CONFIRMATION),
        },
        wave=["input", "ready"],
    )
    agg = _aggregation(state)
    agg.add_missing_fields("input", ["beneficiary_id"])
    agg.add_prompt("I found multiple matches. Which one did you mean?", task_id="input")
    agg.add_details(
        "input",
        {"options": [{"id": "bene-1", "title": "Tolu GTB · GTBank"}]},
    )
    agg.add_confirmation_task("ready")
    runtime = ExecutionWaveRuntime(
        current_wave=["input", "ready"],
        services=OrchestrationServices.empty(),
        accumulator=agg,
        ctx=ExecutionTurnContext(
            state=state,
            config={"configurable": {}},
            services=OrchestrationServices.empty(),
            current_wave_len=2,
            accumulator=agg,
            current_wave_task_ids=["input", "ready"],
        ),
        task_executors={},
        locale="en",
        mandate_gate_accounts=[],
    )

    updates = await finalize_execution_wave_updates(state=state, runtime=runtime)

    assert updates["pending_interrupt"].kind == "input"
    assert all(entry.get("type") != "request_confirmation" for entry in updates["outbox"])
    assert "Confirm Transactions" not in "\n".join(str(entry.get("text") or "") for entry in updates["outbox"])


async def test_finalize_preserves_ready_batch_siblings_when_one_leg_fails() -> None:
    state = _state(
        {
            "failed": TaskSpec(
                id="failed",
                type="transfer",
                stage=TaskStage.FAILED,
                payload={"error": "I couldn't find your Access Bank account."},
            ),
            "input": TaskSpec(
                id="input",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"amount": 2_000, "recipient_name": "Tolu Adebayo"},
            ),
        },
        wave=["failed", "input"],
    )
    agg = _aggregation(state)
    agg.add_missing_fields("input", ["beneficiary_id"])
    agg.add_prompt("Choose a recipient.", task_id="input")
    runtime = ExecutionWaveRuntime(
        current_wave=["failed", "input"],
        services=OrchestrationServices.empty(),
        accumulator=agg,
        ctx=ExecutionTurnContext(
            state=state,
            config={"configurable": {}},
            services=OrchestrationServices.empty(),
            current_wave_len=2,
            accumulator=agg,
            current_wave_task_ids=["failed", "input"],
        ),
        task_executors={},
        locale="en",
        mandate_gate_accounts=[],
    )

    updates = await finalize_execution_wave_updates(state=state, runtime=runtime)

    output = "\n".join(str(entry.get("text") or "") for entry in updates["outbox"])
    assert "I couldn't find your Access Bank account." in output
    assert "Nothing was submitted" not in output
    assert updates["tasks"]["input"].stage == TaskStage.EXTRACTED
    assert updates.get("current_wave_index", 0) == 0
    assert updates["pending_interrupt"].kind == "input"
    assert "still waiting for your input" in output
