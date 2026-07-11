from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.query_followups import (
    _query_followup_bypass_reason,
)


def test_recent_query_context_routes_referential_read_only_followup() -> None:
    reason, detail = _query_followup_bypass_reason(
        message_text="actually show the second one",
        locale="en",
        has_active_query_session=False,
        has_context_frames=True,
    )
    assert reason == "recent_query_context"
    assert detail


def test_recent_query_context_does_not_capture_unrelated_request() -> None:
    reason, _ = _query_followup_bypass_reason(
        message_text="what is the weather",
        locale="en",
        has_active_query_session=False,
        has_context_frames=True,
    )
    assert reason is None
