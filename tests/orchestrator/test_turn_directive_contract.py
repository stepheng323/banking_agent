import pytest
from pydantic import ValidationError

from apps.chat.src.agent.orchestrator.models.turn_directive import (
    RoutingContractError,
    TurnDirective,
    TurnNextStep,
    TurnOutcomeKind,
    route_resolution,
)


def test_turn_directive_round_trips_as_json() -> None:
    directive = route_resolution(
        updates={"final_response": "Done"},
        owner="guardrail",
        decision="unit_response",
        outcome_kind=TurnOutcomeKind.DIRECT_RESPONSE,
        next_step=TurnNextStep.FINALIZE,
        source="unit",
        path_shape="unit_direct",
    ).materialize()["turn_directive"]

    payload = directive.model_dump(mode="json")

    assert TurnDirective.model_validate(payload) == directive
    assert payload["next_step"] == "finalize"


def test_invalid_outcome_transition_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TurnDirective(
            owner="planner",
            decision="bad_handoff",
            outcome_kind=TurnOutcomeKind.PLANNER_HANDOFF,
            next_step=TurnNextStep.ADVANCE,
            source="unit",
            path_shape="unit",
        )


@pytest.mark.parametrize(
    ("updates", "outcome"),
    [
        ({}, TurnOutcomeKind.DIRECT_RESPONSE),
        ({"waves": []}, TurnOutcomeKind.TASK_DISPATCH),
        ({"final_response": "No", "tasks": {}}, TurnOutcomeKind.PLANNER_HANDOFF),
    ],
)
def test_route_resolution_rejects_invalid_payloads(
    updates: dict[str, object], outcome: TurnOutcomeKind
) -> None:
    next_step = {
        TurnOutcomeKind.DIRECT_RESPONSE: TurnNextStep.END,
        TurnOutcomeKind.TASK_DISPATCH: TurnNextStep.ADVANCE,
        TurnOutcomeKind.PLANNER_HANDOFF: TurnNextStep.PLAN,
    }[outcome]
    resolution = route_resolution(
        updates=updates,
        owner="planner",
        decision="invalid_payload",
        outcome_kind=outcome,
        next_step=next_step,
        source="unit",
        path_shape="unit",
    )

    with pytest.raises(RoutingContractError):
        resolution.materialize()


def test_route_resolution_rejects_controlled_field_override() -> None:
    resolution = route_resolution(
        updates={"turn_directive": object(), "final_response": "Done"},
        owner="guardrail",
        decision="override",
        outcome_kind=TurnOutcomeKind.DIRECT_RESPONSE,
        next_step=TurnNextStep.END,
        source="unit",
        path_shape="unit",
    )

    with pytest.raises(RoutingContractError, match="controlled fields"):
        resolution.materialize()

