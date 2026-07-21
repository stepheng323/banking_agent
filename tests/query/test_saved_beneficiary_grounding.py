from datetime import date

from banking.transactions.query.continuations.beneficiary_grounding import (
    ground_unique_saved_recipient,
    recipient_clarification_candidates,
)
from banking.transactions.query.continuations.clarification_state import resolve_selection_clarification
from banking.transactions.query.models.domain import Filters, QueryExecutionContract, QueryIntent
from banking.transactions.query.models.extraction import ClarificationOperation, PendingClarificationState


def _contract(recipient: str) -> QueryExecutionContract:
    return QueryExecutionContract(
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
    pending = PendingClarificationState(
        original_query="When last did I send money to Tolu?",
        current_intent=QueryIntent.TRANSACTION_LIST,
        clarification_type="selection",
        candidate_payloads=[candidate],
        original_operation=ClarificationOperation(grounded_operation="recipient_filter"),
        query_contract=_contract("Tolu").model_dump(mode="json"),
    )

    updates = resolve_selection_clarification(pending, "1", locale="en", session={})

    assert updates is not None
    assert updates["continuation_type"] == "recipient_filter"
    assert updates["query_contract"].filters.counterparty == ["Tolu Adebayo"]
