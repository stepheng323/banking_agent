from apps.core.src.agent.orchestrator.progress import (
    TurnProgressSnapshot,
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

    wait_seconds = seconds_until_progress_eligible(snapshot, now=3.3)
    assert wait_seconds is not None
    assert wait_seconds > 0.0
    assert should_emit_progress(snapshot, now=3.3) is False


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

    assert seconds_until_progress_eligible(snapshot, now=3.8) == 0.0
    assert should_emit_progress(snapshot, now=3.8) is True
