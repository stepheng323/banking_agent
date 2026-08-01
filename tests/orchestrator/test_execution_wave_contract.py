from typing import NoReturn

import pytest

from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.models.turn_directive import (
    RoutingContractError,
    TurnNextStep,
    TurnOutcomeKind,
    build_turn_directive,
)
from apps.chat.src.agent.orchestrator.workflows.execution.node import advance_wave
from apps.chat.src.agent.orchestrator.workflows.execution.wave import engine as execution_wave_engine
from apps.chat.src.agent.orchestrator.workflows.execution.wave.engine import run_execution_wave


def _config() -> dict[str, object]:
    return {"configurable": {}, "recursion_limit": 50}


def _state_with_task(*, task_stage: TaskStage, pending_interrupt: PendingInterrupt | None = None) -> OrchestratorState:
    task = TaskSpec(
        id="task_transfer",
        type="transfer",
        stage=task_stage,
        payload={"amount": "1000"},
    )
    return OrchestratorState(
        user_id="u_execution_wave_contract",
        phone_number="2348011115500",
        channel="whatsapp",
        last_message_text="send money",
        tasks={"task_transfer": task},
        waves=[["task_transfer"]],
        current_wave_index=0,
        pending_interrupt=pending_interrupt,
        loaded_context={"language": "en"},
    )


async def test_execution_wave_pending_interrupt_stops_before_runtime_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    interrupt = PendingInterrupt(
        kind="input",
        task_ids=["task_transfer"],
        fields_by_task={"task_transfer": ["amount"]},
        prompt="Need amount.",
    )
    state = _state_with_task(task_stage=TaskStage.DRAFT, pending_interrupt=interrupt)

    def fail_runtime_setup(*_: object, **__: object) -> NoReturn:
        raise AssertionError("runtime setup should not run with a pending interrupt")

    monkeypatch.setattr(execution_wave_engine, "build_execution_wave_runtime", fail_runtime_setup)

    result = await run_execution_wave(state, _config())

    assert result.phase == "pending_interrupt"
    assert result.worker_phase_entered is False
    assert result.pending_interrupt_kind == "input"
    assert result.updates == {"pending_interrupt": interrupt}


async def test_advance_wave_completed_wave_still_advances_current_wave_index() -> None:
    state = _state_with_task(task_stage=TaskStage.COMPLETED)

    with pytest.raises(RoutingContractError, match="existing turn directive"):
        await advance_wave(state, _config())


async def test_execution_wave_engine_still_advances_current_wave_index() -> None:
    state = _state_with_task(task_stage=TaskStage.COMPLETED)

    result = await run_execution_wave(state, _config())

    assert result.updates["current_wave_index"] == 1


async def test_advance_wave_preserves_route_identity_and_finalizes_completed_work() -> None:
    directive = build_turn_directive(
        owner="semantic_router",
        decision="domain_transfer",
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        target_domain="transfer",
        source="semantic_router",
        path_shape="semantic_router_domain",
    )
    state = _state_with_task(task_stage=TaskStage.COMPLETED).model_copy(update={"turn_directive": directive})

    updates = await advance_wave(state, _config())

    translated = updates["turn_directive"]
    assert translated.owner == "semantic_router"
    assert translated.decision == "domain_transfer"
    assert translated.source == "semantic_router"
    assert translated.outcome_kind == TurnOutcomeKind.TASK_DISPATCH
    assert translated.next_step == TurnNextStep.FINALIZE


async def test_advance_wave_pending_interrupt_ends_without_replacing_route_identity() -> None:
    interrupt = PendingInterrupt(
        kind="input",
        task_ids=["task_transfer"],
        fields_by_task={"task_transfer": ["amount"]},
        prompt="Need amount.",
    )
    directive = build_turn_directive(
        owner="planner",
        decision="transfer",
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        target_domain="transfer",
        source="planner",
        path_shape="planner",
    )
    state = _state_with_task(task_stage=TaskStage.DRAFT, pending_interrupt=interrupt).model_copy(
        update={"turn_directive": directive}
    )

    updates = await advance_wave(state, _config())

    translated = updates["turn_directive"]
    assert translated.owner == "planner"
    assert translated.decision == "transfer"
    assert translated.outcome_kind == TurnOutcomeKind.TASK_DISPATCH
    assert translated.next_step == TurnNextStep.END
