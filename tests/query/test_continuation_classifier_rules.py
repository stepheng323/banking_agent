import pytest

from apps.chat.src.agent.workers.query.continuations.classifier import ContinuationClassifier


def _classifier() -> ContinuationClassifier:
    return ContinuationClassifier()


def test_end_session_phrase_still_hits_guardrail() -> None:
    classifier = _classifier()

    guarded = classifier._guardrail_classify(
        message="thank you",
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

    guarded = classifier._guardrail_classify(
        message=message,
        language="en",
    )

    assert guarded is None
