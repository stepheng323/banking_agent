from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest

from banking.transactions.runtime import scheduled_runs


@dataclass
class _Run:
    status: str = "queued"
    error_message: str | None = None
    transaction_id: str | None = None
    attempt: int | None = None
    completed_at: datetime | None = None


class _ScheduledRunRepo:
    def __init__(self, run: _Run | None):
        self.run = run
        self.seen_id: str | None = None

    async def get_by_id(self, run_id: str) -> _Run | None:
        self.seen_id = run_id
        return self.run


class _Db:
    def __init__(self) -> None:
        self.added: list[_Run] = []

    def add(self, run: _Run) -> None:
        self.added.append(run)


class _UnitOfWork:
    def __init__(self, run: _Run | None):
        self.scheduled_runs = _ScheduledRunRepo(run)
        self.db = _Db()
        self.committed = False

    async def __aenter__(self) -> _UnitOfWork:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True


def test_scheduled_run_meta_helpers_ignore_invalid_shapes() -> None:
    meta = {"schedule_run_id": 123, "run_source": "scheduled"}

    assert scheduled_runs.scheduled_meta({"scheduled_meta": meta}) == meta
    assert scheduled_runs.scheduled_meta({"scheduled_meta": "invalid"}) == {}
    assert scheduled_runs.schedule_run_id(meta) == "123"
    assert scheduled_runs.schedule_run_id({}) is None
    assert scheduled_runs.is_scheduled_run(meta) is True
    assert scheduled_runs.is_scheduled_run({"run_source": "manual"}) is False


@pytest.mark.asyncio
async def test_update_scheduled_run_updates_terminal_run(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _Run(transaction_id="existing")
    uow = _UnitOfWork(run)
    monkeypatch.setattr(scheduled_runs, "UnitOfWork", lambda: uow)

    await scheduled_runs.update_scheduled_run(
        "run-1",
        status="failed",
        error_message="Declined",
        transaction_id="tx-1",
        attempt=2,
    )

    assert uow.scheduled_runs.seen_id == "run-1"
    assert run.status == "failed"
    assert run.error_message == "Declined"
    assert run.transaction_id == "tx-1"
    assert run.attempt == 2
    assert run.completed_at is not None
    assert uow.db.added == [run]
    assert uow.committed is True


@pytest.mark.asyncio
async def test_update_scheduled_run_without_id_skips_unit_of_work(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_factory() -> Any:
        raise AssertionError("UnitOfWork should not be opened without a schedule run id")

    monkeypatch.setattr(scheduled_runs, "UnitOfWork", fail_factory)

    await scheduled_runs.update_scheduled_run(None, status="processing")
