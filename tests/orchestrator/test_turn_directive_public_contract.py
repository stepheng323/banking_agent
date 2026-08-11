from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from apps.chat.src.agent.orchestrator.graph.invocation_context import build_invocation_result
from apps.chat.src.agent.orchestrator.graph.route_metrics import (
    resolve_path_label,
    resolve_semantic_path_shape,
)
from apps.chat.src.agent.orchestrator.models.message_context import MessageContext
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.models.turn_directive import (
    TurnOutcomeKind,
    build_turn_directive,
)
from tests.orchestrator.conversation_harness import _PER_TURN_RESET


def _directive(*, owner: str = "semantic_router"):
    return build_turn_directive(
        owner=owner,  # type: ignore[arg-type]
        decision="domain_query",
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        target_domain="query",
        mode="continuation",
    )


def test_invocation_result_serializes_only_the_nested_route_contract() -> None:
    result = build_invocation_result(
        final_state={
            "outbox": [],
            "final_response": "Here are your transactions.",
            "loaded_context": {"language": "en"},
            "turn_directive": _directive(),
        },
        loaded_context={"language": "en"},
        path_shape="semantic_router_domain",
    )

    assert result["turn_directive"] == {
        "owner": "semantic_router",
        "decision": "domain_query",
        "outcome_kind": "task_dispatch",
        "next_step": "advance",
        "target_domain": "query",
        "mode": "continuation",
        "source": "semantic_router",
        "path_shape": "semantic_router",
        "heuristic_type": None,
        "heuristic_name": None,
    }
    assert not {
        "semantic_path_shape",
        "routing_owner",
        "routing_decision",
        "routing_target_domain",
        "routing_mode",
        "route_source",
    }.intersection(result)
    json.dumps(result["turn_directive"])


def test_path_label_prefers_directive_over_legacy_payload_flags() -> None:
    context = MessageContext(
        phone_number="2348000000000",
        text="show transactions",
        message_id="turn-1",
    )

    assert (
        resolve_path_label(
            context,
            {"turn_directive": _directive(owner="planner"), "direct_path_triggered": True},
        )
        == "planner_path"
    )
    assert (
        resolve_path_label(
            context,
            {"turn_directive": _directive(owner="interrupt"), "direct_path_triggered": True},
        )
        == "interrupt_path"
    )
    assert (
        resolve_path_label(
            context,
            {"turn_directive": _directive(), "direct_path_triggered": False},
        )
        == "direct_path"
    )


def test_semantic_path_fallback_uses_directive_derived_path() -> None:
    context = MessageContext(
        phone_number="2348000000000",
        text="show transactions",
        message_id="turn-1",
    )
    state = {"turn_directive": _directive(owner="planner")}
    path_label = resolve_path_label(context, state)

    assert resolve_semantic_path_shape(context, state, path_label) == "planner"


def test_v1_checkpoint_is_rejected_after_atomic_cutover() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        OrchestratorState.model_validate(
            {
                "schema_version": "v1",
                "user_id": "user-1",
                "phone_number": "2348000000000",
                "routing_owner": "planner",
                "routing_decision": "transfer",
                "turn_directive": _directive().model_dump(mode="json"),
            }
        )


def test_missing_directive_does_not_infer_interrupt_authority() -> None:
    context = MessageContext(
        phone_number="2348000000000",
        text="continue",
        message_id="turn-legacy",
    )

    assert resolve_path_label(context, {"pending_interrupt": {"kind": "input"}}) == "planner_path"


def test_missing_version_with_new_directive_is_not_misclassified_as_v1() -> None:
    state = OrchestratorState.model_validate(
        {
            "user_id": "user-1",
            "phone_number": "2348000000000",
            "turn_directive": _directive().model_dump(mode="json"),
        }
    )

    assert state.schema_version == "v2"
    assert state.turn_directive == _directive()


def test_invalid_directive_is_rejected_after_atomic_cutover() -> None:
    with pytest.raises(ValidationError, match="turn_directive"):
        OrchestratorState.model_validate(
            {
                "schema_version": "v2",
                "user_id": "user-1",
                "phone_number": "2348000000000",
                "active_domain": "query",
                "turn_directive": {"owner": "unknown", "decision": "bad"},
            }
        )


def test_current_checkpoint_round_trip_preserves_directive() -> None:
    state = OrchestratorState(
        user_id="user-current",
        phone_number="2348000000000",
        turn_directive=_directive(),
    )

    restored = OrchestratorState.model_validate(state.model_dump(mode="json"))

    assert restored == state
    assert restored.turn_directive == _directive()


def test_conversation_harness_clears_the_complete_route_contract_each_turn() -> None:
    assert _PER_TURN_RESET["turn_directive"] is None
    assert not {
        "turn_owner",
        "turn_decision",
        "routing_owner",
        "routing_decision",
        "routing_target_domain",
        "routing_mode",
        "route_source",
    }.intersection(_PER_TURN_RESET)
