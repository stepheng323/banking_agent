from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.outcomes import (
    batch_limit_response,
    context_read_shortcut,
    failure_response,
    non_task_response,
    policy_block,
    quoted_replay_dispatch,
    task_dispatch,
    unavailable_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import planner_state_view
from shared.types.planner import PlannerOutput


def _planner_output(primary_intent: str = "transfer") -> PlannerOutput:
    return PlannerOutput(primary_intent=primary_intent)


def _state() -> OrchestratorState:
    return OrchestratorState(
        user_id="u_planner_outcomes",
        phone_number="2348011114400",
        channel="whatsapp",
        last_message_text="send money",
        loaded_context={"language": "en"},
    )


def test_unavailable_and_failure_outcomes_use_planner_route_metadata() -> None:
    unavailable = unavailable_response("en")
    failed = failure_response()

    assert unavailable["final_response"]
    assert unavailable["routing_owner"] == "planner"
    assert unavailable["routing_decision"] == "planner_unavailable"
    assert unavailable["planner_used"] is True
    assert failed["routing_decision"] == "planner_failed"
    assert failed["planner_used"] is True


def test_quoted_replay_and_context_read_outcomes_preserve_payloads() -> None:
    quoted = quoted_replay_dispatch({"tasks": {"quoted": object()}})
    context_read = context_read_shortcut({"final_response": "Here you go."})

    assert quoted["tasks"]
    assert quoted["routing_decision"] == "quoted_replay"
    assert context_read["final_response"] == "Here you go."
    assert context_read["semantic_path_shape"] == "planner"
    assert context_read["routing_decision"] == "planner_context_read"


def test_non_task_policy_and_batch_outcomes_include_standard_metadata() -> None:
    planner_output = _planner_output("support")
    non_task = non_task_response(handled_response={"final_response": "Done."}, planner_output=planner_output)
    blocked = policy_block(
        response="Blocked.",
        normalized_instruction="send money",
        planner_output=planner_output,
        locale_updates={"loaded_context": {"language": "en"}},
    )
    limited = batch_limit_response(
        response="Too many.",
        normalized_instruction="send money",
        planner_output=planner_output,
        locale_updates={"loaded_context": {"language": "en"}},
    )

    assert non_task["routing_decision"] == "support"
    assert non_task["routing_target_domain"] == "support"
    assert blocked["semantic_path_shape"] == "planner_capability_blocked"
    assert blocked["routing_decision"] == "capability_blocked"
    assert blocked["loaded_context"] == {"language": "en"}
    assert limited["semantic_path_shape"] == "planner"
    assert limited["routing_decision"] == "transaction_batch_limit"


def test_task_dispatch_outcome_sets_wave_state_and_preserves_stashed_query_session() -> None:
    task = TaskSpec(
        id="task_transfer",
        type="transfer",
        stage=TaskStage.DRAFT,
        payload={"amount": "1000"},
    )
    state = _state().model_copy(update={"stashed_query_session": {"id": "existing"}})
    updates = task_dispatch(
        task_updates={
            "new_tasks": {"task_transfer": task},
            "waves": [["task_transfer"]],
            "stashed_query_session_update": None,
        },
        planner_output=_planner_output("transfer"),
        normalized_instruction="send 1000",
        policy_notice=None,
        locale_updates={"loaded_context": {"language": "en"}},
        state_view=planner_state_view(state),
    )

    assert updates["tasks"] == {"task_transfer": task}
    assert updates["waves"] == [["task_transfer"]]
    assert updates["current_wave_index"] == 0
    assert updates["stashed_query_session"] == {"id": "existing"}
    assert updates["routing_decision"] == "transfer"
    assert updates["routing_target_domain"] == "transfer"
