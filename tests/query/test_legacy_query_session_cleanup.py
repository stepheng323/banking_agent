import time

from banking.transactions.query.session import SESSION_TTL, is_query_session_stale


def test_query_session_stale_detects_expired_snapshot() -> None:
    session = {"timestamp": time.time() - SESSION_TTL - 1}

    assert is_query_session_stale(session)


def test_query_session_stale_keeps_snapshot_without_timestamp() -> None:
    assert is_query_session_stale({}) is False


def test_query_session_stale_treats_invalid_timestamp_as_stale() -> None:
    assert is_query_session_stale({"timestamp": "not-a-time"})
