from banking.transactions.query.services.reasoning.shortcuts import (
    resolve_query_shortcut_with_reason,
)


def test_query_shortcut_matches_exact_pagination_phrase_for_english() -> None:
    decision, reason = resolve_query_shortcut_with_reason("more", "en")

    assert decision is not None
    assert decision.kind == "pagination"
    assert decision.action == "show_more"
    assert reason == "matched"


def test_query_shortcut_matches_previous_pagination_phrase_for_english() -> None:
    decision, reason = resolve_query_shortcut_with_reason("Previous page", "en")

    assert decision is not None
    assert decision.kind == "pagination"
    assert decision.action == "show_previous"
    assert reason == "matched"


def test_query_shortcut_matches_exact_action_phrase_for_pidgin() -> None:
    decision, reason = resolve_query_shortcut_with_reason("receipt", "pcm")

    assert decision is not None
    assert decision.kind == "actionable"
    assert decision.action == "get_receipt"
    assert reason == "matched"


def test_query_shortcut_detail_request_falls_back_for_semantic_resolver() -> None:
    decision, reason = resolve_query_shortcut_with_reason("show more details", "en")

    assert decision is None
    assert reason == "no_match"


def test_query_shortcut_semantic_followup_falls_back_for_english() -> None:
    decision, reason = resolve_query_shortcut_with_reason("show me", "en")

    assert decision is None
    assert reason == "no_match"


def test_query_shortcut_time_rescope_followup_falls_back_for_english() -> None:
    decision, reason = resolve_query_shortcut_with_reason("What about last week", "en")

    assert decision is None
    assert reason == "no_match"


def test_query_shortcut_unknown_locale_falls_back() -> None:
    decision, reason = resolve_query_shortcut_with_reason("more", "fr")

    assert decision is None
    assert reason == "unsupported_locale"
