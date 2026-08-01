import pytest
from langgraph.graph import END

from apps.chat.src.agent.orchestrator.graph.builder import (
    _route_advance,
    _route_gate,
    _route_interrupt,
    _route_plan,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.models.turn_directive import (
    RoutingContractError,
    TurnNextStep,
    TurnOutcomeKind,
    build_turn_directive,
)


def _state(next_step: TurnNextStep, outcome: TurnOutcomeKind) -> OrchestratorState:
    return OrchestratorState(
        user_id="u-route",
        phone_number="2348000000000",
        turn_directive=build_turn_directive(
            owner="guardrail",
            decision="unit_route",
            outcome_kind=outcome,
            next_step=next_step,
            source="unit",
            path_shape="unit",
        ),
    )


@pytest.mark.parametrize(
    ("next_step", "outcome", "expected"),
    [
        (TurnNextStep.ADVANCE, TurnOutcomeKind.TASK_DISPATCH, "advance"),
        (TurnNextStep.PLAN, TurnOutcomeKind.PLANNER_HANDOFF, "plan"),
        (TurnNextStep.HANDLE_INTERRUPT, TurnOutcomeKind.INTERRUPT_HANDOFF, "handle_interrupt"),
        (TurnNextStep.FINALIZE, TurnOutcomeKind.DIRECT_RESPONSE, "finalize"),
        (TurnNextStep.END, TurnOutcomeKind.DIRECT_RESPONSE, END),
    ],
)
def test_gate_routes_only_from_committed_next_step(
    next_step: TurnNextStep, outcome: TurnOutcomeKind, expected: str
) -> None:
    state = _state(next_step, outcome).model_copy(
        update={
            "final_response": "conflicting payload" if next_step == TurnNextStep.ADVANCE else None,
            "pending_interrupt": None,
            "waves": [] if next_step == TurnNextStep.ADVANCE else [["ignored"]],
        }
    )

    assert _route_gate(state) == expected


def test_node_routers_reject_a_missing_directive() -> None:
    state = OrchestratorState(user_id="u-missing", phone_number="2348000000000")

    for router in (_route_gate, _route_interrupt, _route_plan, _route_advance):
        with pytest.raises(RoutingContractError):
            router(state)


def test_planner_and_execution_use_directive_not_payload_flags() -> None:
    planner_state = _state(TurnNextStep.END, TurnOutcomeKind.DIRECT_RESPONSE).model_copy(
        update={"final_response": None, "waves": [["ignored"]], "pending_interrupt": object()}
    )
    execution_state = _state(TurnNextStep.FINALIZE, TurnOutcomeKind.TASK_DISPATCH).model_copy(
        update={"current_wave_index": 0, "waves": [["still-present"]], "pending_interrupt": object()}
    )

    assert _route_plan(planner_state) == END
    assert _route_advance(execution_state) == "finalize"
