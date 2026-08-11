from types import SimpleNamespace

from scripts.readiness_assertions import assert_readiness_turn
from scripts.readiness_conversation_mutations import mutate_conversation
from scripts.readiness_models import (
    ReadinessEffectExpectation,
    ReadinessExpectation,
    ReadinessStateInvariant,
    ReadinessTurn,
)
from scripts.readiness_state import snapshot_state


def _state() -> SimpleNamespace:
    return SimpleNamespace(
        tasks={
            "t1": SimpleNamespace(
                type="transfer",
                stage=SimpleNamespace(value="awaiting_input"),
                payload={"action": "send_money", "amount": 5000, "is_self": False},
            ),
            "t2": SimpleNamespace(
                type="airtime",
                stage=SimpleNamespace(value="extracted"),
                payload={"action": "buy_airtime", "required_fields": ["amount"]},
            ),
        },
        pending_interrupt=SimpleNamespace(
            kind="input",
            task_ids=["t1", "t2"],
            fields_by_task={"t1": ["recipient"], "t2": ["amount"]},
        ),
        active_domain="transfer",
        pending_query_clarification=None,
        query_frames=[],
        query_session_active=False,
        stashed_sessions=[],
        turn_directive={"path_shape": "batch_input", "owner": "interrupt", "decision": "clarify"},
    )


def test_snapshot_is_semantic_and_does_not_include_task_ids_or_amounts() -> None:
    snapshot = snapshot_state(_state())

    assert snapshot["available"] is True
    assert snapshot["tasks"]["count"] == 2
    assert snapshot["tasks"]["types"] == ("airtime", "transfer")
    assert snapshot["pending_interrupt"] == {"kind": "input", "task_count": 2, "field_count": 2}
    serialized = repr(snapshot)
    assert "t1" not in serialized
    assert "5000" not in serialized


def test_state_invariants_and_effect_contract_are_checked() -> None:
    expectation = ReadinessExpectation(
        state_invariants=(
            ReadinessStateInvariant("tasks.count", value=2),
            ReadinessStateInvariant("pending_interrupt.kind", value="input"),
            ReadinessStateInvariant("active_domain", value="transfer"),
        ),
        effect_expectation=ReadinessEffectExpectation(
            exact_money_movement_jobs=1,
            required_topics=("transfer",),
        ),
    )
    passed, errors = assert_readiness_turn(
        ReadinessTurn("continue", expectation),
        "Review transfer",
        state_snapshot=snapshot_state(_state()),
        async_jobs=(
            {"topic": "execute_transfer"},
        ),
    )

    assert passed, errors


def test_conversation_mutation_preserves_turn_order_and_is_reproducible() -> None:
    from scripts.readiness_models import ReadinessScenario

    scenario = ReadinessScenario(
        id="mutation",
        turns=(
            ReadinessTurn("Send 2k to Tolu"),
            ReadinessTurn("Continue"),
        ),
    )

    mutated = mutate_conversation(scenario, "insert_ack_before_last")
    repeated = mutate_conversation(scenario, "insert_ack_before_last")

    assert [turn.text for turn in mutated.turns] == ["Send 2k to Tolu", "Okay", "Continue"]
    assert mutated == repeated
    assert "conversation_mutated" in mutated.tags


def test_preserve_invariant_compares_previous_snapshot() -> None:
    expectation = ReadinessExpectation(
        state_invariants=(ReadinessStateInvariant("tasks.types", mode="preserve"),)
    )
    current = snapshot_state(_state())
    passed, errors = assert_readiness_turn(
        ReadinessTurn("what is my balance?", expectation),
        "Your balance is available.",
        state_snapshot=current,
        previous_state_snapshot=current,
    )

    assert passed, errors


def test_collection_invariants_support_contains_and_not_contains() -> None:
    expectation = ReadinessExpectation(
        state_invariants=(
            ReadinessStateInvariant("tasks.types", mode="contains", value="transfer"),
            ReadinessStateInvariant("tasks.types", mode="not_contains", value="query"),
        )
    )
    passed, errors = assert_readiness_turn(
        ReadinessTurn("continue", expectation),
        "Review transfer",
        state_snapshot=snapshot_state(_state()),
    )

    assert passed, errors
