from apps.core.src.agent.orchestrator.progress import (
    TurnProgressSnapshot,
    is_progress_stage_user_visible,
    render_progress_message,
    seconds_until_progress_eligible,
    should_emit_progress,
)


def test_progress_waits_for_stage_age_even_after_global_threshold() -> None:
    snapshot = TurnProgressSnapshot(
        stage_key="query.fetching_transactions",
        started_at=0.0,
        stage_started_at=3.0,
        last_progress_sent_at=None,
        progress_count=0,
        stage_metadata=None,
        locale="en",
    )

    wait_seconds = seconds_until_progress_eligible(snapshot, now=3.6)
    assert wait_seconds is not None
    assert wait_seconds > 0.0
    assert should_emit_progress(snapshot, now=3.6) is False


def test_progress_waits_until_earlier_first_threshold() -> None:
    snapshot = TurnProgressSnapshot(
        stage_key="query.fetching_transactions",
        started_at=0.0,
        stage_started_at=0.0,
        last_progress_sent_at=None,
        progress_count=0,
        stage_metadata=None,
        locale="en",
    )

    wait_seconds = seconds_until_progress_eligible(snapshot, now=1.4)
    assert wait_seconds is not None
    assert wait_seconds > 0.0
    assert should_emit_progress(snapshot, now=1.4) is False


def test_progress_emits_once_execution_stage_has_been_active_long_enough() -> None:
    snapshot = TurnProgressSnapshot(
        stage_key="query.fetching_transactions",
        started_at=0.0,
        stage_started_at=3.0,
        last_progress_sent_at=None,
        progress_count=0,
        stage_metadata=None,
        locale="en",
    )

    assert seconds_until_progress_eligible(snapshot, now=4.1) == 0.0
    assert should_emit_progress(snapshot, now=4.1) is True


def test_progress_non_visible_stage_never_emits_visible_progress() -> None:
    snapshot = TurnProgressSnapshot(
        stage_key="query.resolving_followup",
        started_at=0.0,
        stage_started_at=0.0,
        last_progress_sent_at=None,
        progress_count=0,
        stage_metadata={"scope_label": "what you sent to mum"},
        locale="en",
    )

    assert is_progress_stage_user_visible("query.resolving_followup") is False
    assert seconds_until_progress_eligible(snapshot, now=8.0) is None
    assert should_emit_progress(snapshot, now=8.0) is False


def test_transfer_only_processing_stage_is_user_visible() -> None:
    assert is_progress_stage_user_visible("transfer.resolving_recipient") is False
    assert is_progress_stage_user_visible("transfer.confirming_details") is False
    assert is_progress_stage_user_visible("transfer.authorizing_transfer") is False
    assert is_progress_stage_user_visible("transfer.processing_transfer") is True


def test_progress_renders_context_aware_query_followup_message() -> None:
    text = render_progress_message(
        stage_key="query.resolving_followup",
        progress_count=0,
        locale="en",
        stage_metadata={"scope_label": "what you sent to mum"},
    )

    assert text == "Let me check what you sent to mum."


def test_progress_renders_context_aware_query_fetch_message() -> None:
    text = render_progress_message(
        stage_key="query.fetching_transactions",
        progress_count=1,
        locale="en",
        stage_metadata={
            "scope_label": "what you sent to mum from Mar 9 to Mar 15",
            "counterparty_label": "mum",
            "direction": "sent",
            "time_label": "from Mar 9 to Mar 15",
        },
    )

    assert text == "Still checking what you sent to mum."


def test_progress_falls_back_to_generic_when_scope_missing() -> None:
    text = render_progress_message(
        stage_key="query.resolving_followup",
        progress_count=0,
        locale="en",
        stage_metadata=None,
    )

    assert text == "Let me check that."
