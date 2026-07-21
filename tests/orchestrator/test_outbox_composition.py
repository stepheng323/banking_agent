"""Pure contract tests for final outbox composition selection."""

from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_finalize import (
    ResponseCompositionPlan,
    _composition_plan,
    _strip_private_outbox_metadata,
)


def test_structured_or_actionable_say_entries_are_never_bridged() -> None:
    assert _composition_plan(
        [
            {"type": "say", "text": "Balance", "body_blocks": [{"type": "heading", "text": "Balances"}]},
            {"type": "say", "text": "A reminder"},
        ]
    ) is ResponseCompositionPlan.PRESERVE_SEPARATE
    assert _composition_plan(
        [
            {"type": "say", "text": "Review", "actionable_payload": {"kind": "confirmation"}},
            {"type": "say", "text": "A reminder"},
        ]
    ) is ResponseCompositionPlan.PRESERVE_SEPARATE


def test_annotated_stackable_fragments_do_not_call_the_bridge() -> None:
    entries = [
        {
            "type": "say",
            "text": "Your balance is ready.",
            "_composition": {"response_family": "balance", "response_shape": "fact_value", "merge_mode": "stackable"},
        },
        {
            "type": "say",
            "text": "Your transfer is waiting.",
            "_composition": {"response_family": "pending_flow", "response_shape": "recap", "merge_mode": "stackable"},
        },
    ]

    assert _composition_plan(entries) is ResponseCompositionPlan.DETERMINISTIC_STACK


def test_private_composition_metadata_never_reaches_delivery() -> None:
    assert _strip_private_outbox_metadata(
        {"type": "say", "text": "Done", "_composition": {"merge_mode": "stackable"}}
    ) == {"type": "say", "text": "Done"}
