from datetime import date

from apps.core.src.agent.graphs.query.models import QueryResultItem, ResultSurface, SurfaceType
from apps.core.src.agent.graphs.query.services.continuity import ContinuationClassifier


def _classifier() -> ContinuationClassifier:
    return ContinuationClassifier(object())


def test_summary_show_my_transactions_is_expand_guardrail() -> None:
    classifier = _classifier()
    surface = ResultSurface(type=SurfaceType.SUMMARY, items=[], context={"view": "summary"})

    guarded = classifier._guardrail_classify(
        message="show my transactions",
        today=date.today().isoformat(),
        items=None,
        surface=surface,
        language="en",
    )

    assert guarded is not None
    continuation_type, data = guarded
    assert continuation_type == "expand"
    assert data["reason"] == "deterministic_expand"


def test_recipient_ranking_followup_forces_new_query_override() -> None:
    classifier = _classifier()

    guarded = classifier._guardrail_classify(
        message="Who did I send money to the most this week",
        today="2026-03-07",
        items=None,
        surface=None,
        language="en",
    )

    assert guarded is not None
    continuation_type, data = guarded
    assert continuation_type == "new_query"
    assert data["reason"] == "deterministic_recipient_ranking_new_query"
    assert data["is_new_query_override"] is True
    assert data["restates_query"] is True


def test_aggregate_phrases_hit_guardrail() -> None:
    classifier = _classifier()

    guarded = classifier._guardrail_classify(
        message="How much have I spent today",
        today=date.today().isoformat(),
        items=None,
        surface=None,
        language="en",
    )

    assert guarded is not None
    continuation_type, data = guarded
    assert continuation_type == "aggregate"
    assert data["reason"] == "deterministic_aggregate"
    assert data["confidence"] == 0.95


def test_time_delta_shortcut_for_yesterday_followup() -> None:
    classifier = _classifier()

    guarded = classifier._guardrail_classify(
        message="what about yesterday",
        today="2026-03-06",
        items=None,
        surface=None,
        language="en",
    )

    assert guarded is not None
    continuation_type, data = guarded
    assert continuation_type == "time_delta"
    assert data["reason"] == "deterministic_time_delta"
    assert data["delta_type"] == "time"
    assert data["time_range"].start.isoformat() == "2026-03-05"
    assert data["time_range"].end.isoformat() == "2026-03-05"


def test_filter_delta_shortcut_for_credit_debit_followups() -> None:
    classifier = _classifier()

    guarded = classifier._guardrail_classify(
        message="only debits",
        today="2026-03-06",
        items=None,
        surface=None,
        language="en",
    )

    assert guarded is not None
    continuation_type, data = guarded
    assert continuation_type == "filter_delta"
    assert data["reason"] == "deterministic_tx_type_filter"
    assert data["delta_type"] == "filter"
    assert data["filters"].transaction_type == "debit"

    guarded2 = classifier._guardrail_classify(
        message="what about credits",
        today="2026-03-06",
        items=None,
        surface=None,
        language="en",
    )

    assert guarded2 is not None
    _, data2 = guarded2
    assert data2["filters"].transaction_type == "credit"


def test_beneficiary_summary_name_reply_maps_to_recipient_drilldown() -> None:
    classifier = _classifier()
    surface = ResultSurface(type=SurfaceType.SUMMARY, items=[], context={"view": "beneficiary_summary"})
    items = [QueryResultItem(description="Gaines", amount=25000, date=date(2026, 3, 10))]

    guarded = classifier._guardrail_classify(
        message="Gaines.",
        today="2026-03-10",
        items=items,
        surface=surface,
        language="en",
    )

    assert guarded is not None
    continuation_type, data = guarded
    assert continuation_type == "recipient_drill_down"
    assert data["reason"] == "deterministic_recipient_drill_down"
    assert data["recipient_name"] == "Gaines"
