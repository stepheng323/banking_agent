from datetime import date

import pytest

from banking.transactions.query.continuations.beneficiary_grounding import (
    ground_unique_saved_recipient,
    recipient_clarification_candidates,
)
from banking.transactions.query.continuations.clarification_state import resolve_selection_clarification
from banking.transactions.query.models.conversation import PendingFieldClarification
from banking.transactions.query.models.domain import Filters, QueryIntent, QueryRequest
from banking.transactions.query.models.extraction import ClarificationOperation
from banking.transactions.query.nodes.extraction import ExtractionStep
from banking.transactions.query.session_state import (
    build_query_session_v3,
    restore_query_session_v3,
)
from tests.query.factories import make_pending_input, make_query_request


class _DummyStructured:
    async def ainvoke(self, prompt: str) -> object:
        del prompt
        raise AssertionError("numeric clarification selection must not invoke the reasoner")


class _DummyLLM:
    def with_structured_output(self, schema: object) -> _DummyStructured:
        del schema
        return _DummyStructured()


def _contract(recipient: str) -> QueryRequest:
    return make_query_request(
        intent=QueryIntent.TRANSACTION_LIST,
        time_start=date(2026, 7, 1),
        time_end=date(2026, 7, 19),
        filters=Filters(transaction_type="debit", counterparty=[recipient]),
    )


_BENEFICIARIES = [
    {
        "id": "bene-gtb",
        "alias": "Tolu GTB",
        "account_name": "Tolu Adeyemi",
        "bank_name": "GTBank",
        "account_number": "2010000002",
        "beneficiary_type": "transfer",
    },
    {
        "id": "bene-first",
        "alias": "Tolu First",
        "account_name": "Tolulope Johnson",
        "bank_name": "First Bank",
        "account_number": "2010000003",
        "beneficiary_type": "transfer",
    },
    {
        "id": "bene-access",
        "alias": "Tolu Access",
        "account_name": "Tolu Adebayo",
        "bank_name": "Access Bank",
        "account_number": "2010000001",
        "beneficiary_type": "transfer",
    },
]


def test_broad_saved_recipient_query_requires_selection() -> None:
    candidates = recipient_clarification_candidates(_contract("Tolu"), _BENEFICIARIES)

    assert len(candidates) == 3
    assert {candidate.payload.entity_id for candidate in candidates} == {
        "bene-gtb",
        "bene-first",
        "bene-access",
    }
    assert all(candidate.payload.filters_patch["counterparty"] for candidate in candidates)


def test_exact_saved_alias_uses_canonical_counterparty_name() -> None:
    grounded = ground_unique_saved_recipient(_contract("Tolu Access"), _BENEFICIARIES)

    assert grounded.filters is not None
    assert grounded.filters.counterparty == ["Tolu Adebayo"]
    assert recipient_clarification_candidates(grounded, _BENEFICIARIES) == []


def test_selected_saved_recipient_resumes_query_with_exact_filter() -> None:
    candidate = recipient_clarification_candidates(_contract("Tolu"), _BENEFICIARIES)[2]
    pending = make_pending_input(
        original_query="When last did I send money to Tolu?",
        clarification_type="selection",
        candidate_payloads=[candidate],
        original_operation=ClarificationOperation(grounded_operation="recipient_filter"),
        query_request=_contract("Tolu"),
    )

    updates = resolve_selection_clarification(pending, "1", locale="en", session={})

    assert updates is not None
    assert updates["continuation_type"] == "recipient_filter"
    assert updates["query_request"].filters.counterparty == ["Tolu Adebayo"]
    assert updates["execute_query_plan"] is True
    assert updates["execution_contract"].request.filters.counterparty == ["Tolu Adebayo"]
    assert updates["query_result"] is None


def test_selection_type_survives_v3_checkpoint_round_trip() -> None:
    request = _contract("Tolu")
    candidate = recipient_clarification_candidates(request, _BENEFICIARIES)[0]
    pending = make_pending_input(
        original_query="When last did I send money to Tolu?",
        clarification_type="selection",
        candidate_payloads=[candidate],
        original_operation=ClarificationOperation(grounded_operation="recipient_filter"),
        query_request=request,
    )

    session = build_query_session_v3(
        request=None,
        result=None,
        raw_frames=[],
        pending_input=pending,
    )
    restored_session = restore_query_session_v3(session.model_dump(mode="json"))
    assert restored_session is not None
    restored = restored_session.pending_input
    assert isinstance(restored, PendingFieldClarification)
    assert restored.clarification_type == "selection"
    updates = resolve_selection_clarification(restored, "1", locale="en", session={})
    assert updates is not None
    assert updates["query_request"].filters.counterparty == ["Tolu Adeyemi"]


@pytest.mark.asyncio
async def test_v3_numeric_selection_is_resolved_before_reasoner() -> None:
    request = _contract("Tolu")
    candidate = recipient_clarification_candidates(request, _BENEFICIARIES)[0]
    pending = make_pending_input(
        original_query="When last did I send money to Tolu?",
        clarification_type="selection",
        candidate_payloads=[candidate],
        original_operation=ClarificationOperation(grounded_operation="recipient_filter"),
        query_request=request,
    )
    session = build_query_session_v3(
        request=None,
        result=None,
        raw_frames=[],
        pending_input=pending,
    )

    step = ExtractionStep(_DummyLLM())
    result = await step.run(
        {
            "message": "1",
            "language": "en",
            "today": date(2026, 7, 19),
            "query_session": session.model_dump(mode="json"),
        }
    )

    assert result.outcome.value == "ok"
    assert result.patch["flow_state"] == "executing"
    assert result.patch["query_request"].filters.counterparty == ["Tolu Adeyemi"]
