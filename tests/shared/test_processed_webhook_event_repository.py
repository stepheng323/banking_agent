from types import SimpleNamespace

import pytest

from shared.repositories.processed_webhook_event_repository import ProcessedWebhookEventRepository


class _FakeScalarResult:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class _FakeExecuteResult:
    def __init__(self, row):
        self.row = row

    def scalars(self):
        return _FakeScalarResult(self.row)


class _FakeDb:
    def __init__(self, row):
        self.row = row
        self.queries = []
        self.added = []
        self.flush_calls = 0

    async def execute(self, query):
        self.queries.append(query)
        return _FakeExecuteResult(self.row)

    def add(self, row) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        self.flush_calls += 1


def _event(status: str):
    return SimpleNamespace(
        status=status,
        event_name="transfer.completed",
        payload_hash="old-hash",
        attempt_count=1,
        last_seen_at=None,
        error_message="old error",
        processed_at=None,
    )


def _last_query_was_for_update(db: _FakeDb) -> bool:
    return getattr(db.queries[-1], "_for_update_arg", None) is not None


@pytest.mark.asyncio
async def test_claim_locks_existing_failed_event_before_reclaim() -> None:
    row = _event("failed")
    db = _FakeDb(row)
    repo = ProcessedWebhookEventRepository(db)

    claimed = await repo.claim(
        provider="flutterwave",
        event_id="evt-1",
        event_name="transfer.completed",
        payload_hash="new-hash",
    )

    assert claimed is True
    assert _last_query_was_for_update(db)
    assert row.status == "processing"
    assert row.event_name == "transfer.completed"
    assert row.payload_hash == "new-hash"
    assert row.attempt_count == 2
    assert row.error_message is None
    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_claim_locks_and_rejects_existing_processed_event() -> None:
    row = _event("processed")
    db = _FakeDb(row)
    repo = ProcessedWebhookEventRepository(db)

    claimed = await repo.claim(
        provider="flutterwave",
        event_id="evt-1",
        event_name="transfer.completed",
        payload_hash="new-hash",
    )

    assert claimed is False
    assert _last_query_was_for_update(db)
    assert row.status == "processed"
    assert db.flush_calls == 0


@pytest.mark.asyncio
async def test_mark_failed_locks_and_does_not_downgrade_processed_event() -> None:
    row = _event("processed")
    db = _FakeDb(row)
    repo = ProcessedWebhookEventRepository(db)

    result = await repo.mark_failed(provider="flutterwave", event_id="evt-1", error_message="late error")

    assert result is row
    assert _last_query_was_for_update(db)
    assert row.status == "processed"
    assert row.error_message == "old error"
    assert db.flush_calls == 0


@pytest.mark.asyncio
async def test_mark_processed_locks_existing_event() -> None:
    row = _event("processing")
    db = _FakeDb(row)
    repo = ProcessedWebhookEventRepository(db)

    result = await repo.mark_processed(provider="flutterwave", event_id="evt-1")

    assert result is row
    assert _last_query_was_for_update(db)
    assert row.status == "processed"
    assert row.processed_at is not None
    assert row.error_message is None
    assert db.flush_calls == 1
