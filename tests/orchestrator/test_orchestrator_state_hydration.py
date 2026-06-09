from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from shared.types.planner import PlannerOutput


def test_legacy_planner_output_missing_primary_intent_is_dropped() -> None:
    state = OrchestratorState(
        user_id="user-1",
        phone_number="2348000000001",
        planner_output={
            "lc": 2,
            "type": "constructor",
            "tasks": [],
            "confidence": 0.9,
        },
    )

    assert state.planner_output is None


def test_valid_planner_output_is_preserved() -> None:
    planner_output = PlannerOutput(primary_intent="conversational", response="Hello")

    state = OrchestratorState(
        user_id="user-1",
        phone_number="2348000000001",
        planner_output=planner_output,
    )

    assert state.planner_output == planner_output
