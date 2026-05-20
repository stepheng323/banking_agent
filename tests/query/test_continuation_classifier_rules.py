from datetime import date

import pytest

from apps.chat.src.agent.graphs.query.models import Filters, QueryExecutionContract, QueryIntent, QueryResultItem
from apps.chat.src.agent.graphs.query.services.continuity import ContinuationClassifier
from apps.chat.src.agent.shared.query_contracts import SurfaceView, SurfaceViewMode


def _classifier() -> ContinuationClassifier:
    return ContinuationClassifier()


def test_end_session_phrase_still_hits_guardrail() -> None:
    classifier = _classifier()

    guarded = classifier._guardrail_classify(
        message="thank you",
        items=None,
        surface_view=None,
        language="en",
    )

    assert guarded is not None
    continuation_type, data = guarded
    assert continuation_type == "end_session"
    assert data["reason"] == "deterministic_end_session"


def test_end_session_phrase_with_emoji_still_hits_guardrail() -> None:
    classifier = _classifier()

    guarded = classifier._guardrail_classify(
        message="thank you 😊",
        items=None,
        surface_view=None,
        language="en",
    )

    assert guarded is not None
    continuation_type, data = guarded
    assert continuation_type == "end_session"
    assert data["reason"] == "deterministic_end_session"


@pytest.mark.parametrize(
    "message",
    [
        "get out",
        "Gaines.",
        "When was Kunle's transaction?",
        "What about tolu?",
        "send again",
        "show my recent transactions",
        "show today's transaction",
        "what about credits",
    ],
)
def test_semantic_query_followups_do_not_hit_structural_guardrail(message: str) -> None:
    classifier = _classifier()
    surface_view = SurfaceView(mode=SurfaceViewMode.GROUPED_SUMMARY, context={"view": "beneficiary_summary"})
    items = [QueryResultItem(description="Gaines", amount=25000, date=date(2026, 3, 10))]
    query_contract = QueryExecutionContract(
        intent=QueryIntent.TRANSACTION_SEARCH,
        time_start=date(2026, 4, 1),
        time_end=date(2026, 4, 6),
        filters=Filters(counterparty=["Mum"], transaction_type="debit"),
        result_limit=1,
        result_reference="latest",
    )

    guarded = classifier._guardrail_classify(
        message=message,
        items=items,
        surface_view=surface_view,
        language="en",
        query_contract=query_contract,
    )

    assert guarded is None
