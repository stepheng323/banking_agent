from banking.runtime.results import TransactionOutcome
from banking.transactions.query.continuations.clarification_state import (
    build_selection_clarification_updates,
    clarification_candidate,
    resolve_selection_clarification,
)
from banking.transactions.query.contracts import SelectionPayload
from banking.transactions.query.models.extraction import (
    ClarificationOperation,
    PendingClarificationState,
)


def _payload(entity_id: str, label: str) -> SelectionPayload:
    return SelectionPayload(
        selection_kind="transaction",
        entity_type="transaction",
        entity_id=entity_id,
        label=label,
    )


def _pending() -> PendingClarificationState:
    updates = build_selection_clarification_updates(
        candidates=[
            clarification_candidate(payload=_payload("tx-1", "Ada transfer"), label="Ada transfer"),
            clarification_candidate(payload=_payload("tx-2", "Ado transfer"), label="Ado transfer", frame_id="qf_2"),
        ],
        operation=ClarificationOperation(
            continuation_type="drill_down",
            drill_down_action="answer_fact",
            fact_field="bank",
        ),
        query_request=None,
        locale="en",
        session={"current_page": 1},
        turn_id="turn-1",
    )
    return PendingClarificationState.model_validate(updates["pending_clarification"])


def test_numeric_selection_restores_original_fact_operation() -> None:
    updates = resolve_selection_clarification(_pending(), "2", locale="en", session={})
    assert updates is not None
    assert updates["selected_item_id"] == "tx-2"
    assert updates["selected_frame_id"] == "qf_2"
    assert updates["drill_down_action"] == "answer_fact"
    assert updates["fact_field"] == "bank"


def test_ambiguous_fuzzy_selection_reprompts_then_exhausts() -> None:
    pending = _pending()
    first = resolve_selection_clarification(pending, "transfer", locale="en", session={})
    assert first is not None
    assert first["transaction_outcome"] == TransactionOutcome.NEEDS_INPUT
    reprompted = PendingClarificationState.model_validate(first["pending_clarification"])
    assert reprompted.attempt_count == 1

    second = resolve_selection_clarification(reprompted, "still not sure", locale="en", session={})
    assert second is not None
    assert second["pending_clarification"] is None


def test_neither_cancels_selection() -> None:
    updates = resolve_selection_clarification(_pending(), "neither", locale="en", session={})
    assert updates is not None
    assert updates["transaction_outcome"] == TransactionOutcome.OK
    assert updates["session_active"] is False


def test_legacy_pending_clarification_remains_valid() -> None:
    pending = PendingClarificationState.model_validate(
        {
            "original_query": "show spending recently",
            "current_intent": "analytics_summary",
            "language": "en",
        }
    )
    assert pending.clarification_type is None
    assert pending.candidate_payloads == []
