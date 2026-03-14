from datetime import UTC, datetime

from shared.repositories.transaction_repository import normalize_db_timestamp


def test_normalize_db_timestamp_strips_timezone_to_utc_naive() -> None:
    aware = datetime(2026, 3, 14, 19, 48, 45, tzinfo=UTC)

    normalized = normalize_db_timestamp(aware)

    assert normalized.tzinfo is None
    assert normalized == datetime(2026, 3, 14, 19, 48, 45)


def test_normalize_db_timestamp_leaves_naive_value_unchanged() -> None:
    naive = datetime(2026, 3, 14, 19, 48, 45)

    normalized = normalize_db_timestamp(naive)

    assert normalized is naive
