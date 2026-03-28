from datetime import date

import pytest

from apps.core.src.agent.graphs.query.models import QueryResultItem, ResultSurface, SurfaceType
from apps.core.src.agent.graphs.query.services.continuity import ContinuationClassifier


def _classifier() -> ContinuationClassifier:
    return ContinuationClassifier()


def test_end_session_phrase_still_hits_guardrail() -> None:
    classifier = _classifier()

    guarded = classifier._guardrail_classify(
        message="thank you",
        items=None,
        surface=None,
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
        surface=None,
        language="en",
    )

    assert guarded is not None
    continuation_type, data = guarded
    assert continuation_type == "end_session"
    assert data["reason"] == "deterministic_end_session"


def test_dismissive_turn_does_not_hit_deterministic_guardrail() -> None:
    classifier = _classifier()

    guarded = classifier._guardrail_classify(
        message="get out",
        items=None,
        surface=None,
        language="en",
    )

    assert guarded is None


def test_beneficiary_summary_name_reply_maps_to_recipient_drilldown() -> None:
    classifier = _classifier()
    surface = ResultSurface(type=SurfaceType.SUMMARY, items=[], context={"view": "beneficiary_summary"})
    items = [QueryResultItem(description="Gaines", amount=25000, date=date(2026, 3, 10))]

    guarded = classifier._guardrail_classify(
        message="Gaines.",
        items=items,
        surface=surface,
        language="en",
    )

    assert guarded is not None
    continuation_type, data = guarded
    assert continuation_type == "recipient_drill_down"
    assert data["reason"] == "deterministic_recipient_drill_down"
    assert data["recipient_name"] == "Gaines"


def test_beneficiary_summary_fact_followup_maps_to_recipient_drilldown_with_fact_field() -> None:
    classifier = _classifier()
    surface = ResultSurface(type=SurfaceType.SUMMARY, items=[], context={"view": "beneficiary_summary"})
    items = [QueryResultItem(description="Adesanya Kunle", amount=50000, date=date(2026, 3, 27))]

    guarded = classifier._guardrail_classify(
        message="When was Kunle's transaction?",
        items=items,
        surface=surface,
        language="en",
    )

    assert guarded is not None
    continuation_type, data = guarded
    assert continuation_type == "recipient_drill_down"
    assert data["reason"] == "deterministic_recipient_fact_drill_down"
    assert data["recipient_name"] == "Adesanya Kunle"
    assert data["fact_field"] == "date"


def test_retransfer_phrase_maps_to_drill_down_for_list_surface() -> None:
    classifier = _classifier()
    surface = ResultSurface(type=SurfaceType.LIST, items=[], context={"type": "transaction_list"})

    guarded = classifier._guardrail_classify(
        message="send again",
        items=None,
        surface=surface,
        language="en",
    )

    assert guarded is not None
    continuation_type, data = guarded
    assert continuation_type == "drill_down"
    assert data["reason"] == "deterministic_retransfer"
    assert data["drill_down_action"] == "re_transfer"


@pytest.mark.parametrize(
    "message",
    [
        "show me",
        "How much have I spent today",
        "Who did I send money to the most this week",
        "show my transactions",
    ],
)
def test_scope_and_pagination_phrases_no_longer_hit_guardrail(message: str) -> None:
    classifier = _classifier()
    surface = ResultSurface(type=SurfaceType.SUMMARY, items=[], context={"view": "summary"})

    guarded = classifier._guardrail_classify(
        message=message,
        items=None,
        surface=surface,
        language="en",
    )

    assert guarded is None
