from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from apps.chat.src.agent.workers.query.handlers.analytics import _aggregate_breakdown
from apps.chat.src.agent.workers.query.models.domain import (
    Aggregation,
    Filters,
    QueryIntent,
    QueryIR,
    TimeRange,
)
from apps.chat.src.agent.workers.query.services.fetching.fetch import (
    _is_missing_mirror_table_error,
    fetch_and_filter,
    fetch_transactions_base,
)
from shared.clients.abstractions.banking import TransactionData, TransactionPageData


def _query_ir(**kwargs: object) -> QueryIR:
    fallback_day = date(2026, 3, 28)
    defaults: dict[str, object] = {
        "intent": QueryIntent.TRANSACTION_LIST,
        "time_range": TimeRange(start=fallback_day, end=fallback_day),
    }
    defaults.update(kwargs)
    return QueryIR(**defaults)


def test_missing_mirror_table_error_is_detected_without_full_sqlalchemy_exception() -> None:
    error = RuntimeError('relation "bank_transaction_coverage" does not exist')

    assert _is_missing_mirror_table_error(error) is True


class _FakeDbSession:
    def add(self, _obj: Any) -> None:
        return None

    async def flush(self) -> None:
        return None


class _FakeLocalTransactionsRepo:
    async def get_by_user(self, user_id: str, limit: int = 20) -> list[Any]:
        del user_id, limit
        return []


class _FakeAccountsRepo:
    def __init__(self, state: _MirrorState) -> None:
        self._state = state

    async def get_by_account_id(self, account_id: str) -> Any | None:
        return self._state.accounts.get(account_id)


class _FakeBankTransactionsRepo:
    def __init__(self, state: _MirrorState) -> None:
        self._state = state

    async def bulk_upsert(self, rows: list[dict]) -> int:
        for row in rows:
            key = (str(row["linked_account_id"]), str(row["provider_transaction_id"]))
            existing = self._state.bank_transactions.get(key)
            payload = dict(row)
            if existing is not None:
                payload["first_seen_at"] = existing.first_seen_at
            self._state.bank_transactions[key] = SimpleNamespace(**payload)
        return len(rows)

    async def list_by_accounts_window(
        self,
        linked_account_ids: list[str],
        *,
        start_date: date,
        end_date: date,
        provider: str = "mono",
    ) -> list[Any]:
        del provider
        rows = []
        for row in self._state.bank_transactions.values():
            if str(row.linked_account_id) not in {str(item) for item in linked_account_ids}:
                continue
            if row.posted_date < start_date or row.posted_date > end_date:
                continue
            rows.append(row)
        rows.sort(key=lambda item: (item.posted_at, item.provider_transaction_id), reverse=True)
        return rows

    async def get_latest_posted_at(self, linked_account_id: str, *, provider: str = "mono") -> datetime | None:
        del provider
        matches = [
            row.posted_at
            for row in self._state.bank_transactions.values()
            if str(row.linked_account_id) == str(linked_account_id)
        ]
        return max(matches) if matches else None


class _FakeCoverageRepo:
    def __init__(self, state: _MirrorState) -> None:
        self._state = state

    async def find_missing_gaps(
        self,
        linked_account_id: str,
        *,
        start_date: date,
        end_date: date,
        provider: str = "mono",
        coverage_type: str = "full",
    ) -> list[tuple[date, date]]:
        del provider, coverage_type
        windows = sorted(self._state.coverage.get(str(linked_account_id), []))
        cursor = start_date
        gaps: list[tuple[date, date]] = []
        for window_start, window_end in windows:
            if window_end < cursor:
                continue
            if window_start > end_date:
                break
            if window_start > cursor:
                gaps.append((cursor, min(end_date, window_start.fromordinal(window_start.toordinal() - 1))))
            cursor = max(cursor, window_end.fromordinal(window_end.toordinal() + 1))
            if cursor > end_date:
                break
        if cursor <= end_date:
            gaps.append((cursor, end_date))
        return gaps

    async def is_window_covered(
        self,
        linked_account_id: str,
        *,
        start_date: date,
        end_date: date,
        provider: str = "mono",
        coverage_type: str = "full",
    ) -> bool:
        return not await self.find_missing_gaps(
            linked_account_id,
            start_date=start_date,
            end_date=end_date,
            provider=provider,
            coverage_type=coverage_type,
        )

    async def add_full_coverage(
        self,
        linked_account_id: str,
        *,
        start_date: date,
        end_date: date,
        provider: str = "mono",
    ) -> Any:
        del provider
        windows = list(self._state.coverage.get(str(linked_account_id), []))
        windows.append((start_date, end_date))
        windows.sort()

        merged: list[tuple[date, date]] = []
        for window_start, window_end in windows:
            if not merged:
                merged.append((window_start, window_end))
                continue
            current_start, current_end = merged[-1]
            if window_start <= current_end.fromordinal(current_end.toordinal() + 1):
                merged[-1] = (current_start, max(current_end, window_end))
            else:
                merged.append((window_start, window_end))
        self._state.coverage[str(linked_account_id)] = merged
        return SimpleNamespace(
            linked_account_id=linked_account_id,
            window_start=merged[-1][0],
            window_end=merged[-1][1],
        )


class _MirrorState:
    def __init__(self) -> None:
        self.bank_transactions: dict[tuple[str, str], Any] = {}
        self.coverage: dict[str, list[tuple[date, date]]] = {}
        self.accounts: dict[str, Any] = {}


class _FakeUnitOfWork:
    def __init__(self, state: _MirrorState) -> None:
        self.db = _FakeDbSession()
        self.bank_transactions = _FakeBankTransactionsRepo(state)
        self.bank_transaction_coverages = _FakeCoverageRepo(state)
        self.accounts = _FakeAccountsRepo(state)
        self.transactions = _FakeLocalTransactionsRepo()

    async def __aenter__(self) -> _FakeUnitOfWork:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        del exc_type, exc, tb
        return None


class _PagedProvider:
    def __init__(self, pages: dict[tuple[str, str, str, int], list[TransactionData]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, str, str, int, int]] = []

    async def get_transactions_page(
        self,
        account_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
        page: int = 1,
        user_id: str | None = None,
        mock_account_slot: int | None = None,
    ) -> TransactionPageData:
        del user_id, mock_account_slot
        assert start_date is not None
        assert end_date is not None
        self.calls.append((account_id, start_date, end_date, page, limit))
        items = self.pages.get((account_id, start_date, end_date, page), [])
        has_more = (account_id, start_date, end_date, page + 1) in self.pages
        return TransactionPageData(
            transactions=items,
            page=page,
            has_more=has_more,
            next_page=page + 1 if has_more else None,
        )

    async def get_transactions(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        return []


def _query(
    *,
    start_date: date,
    end_date: date,
    filters: Filters | None = None,
) -> QueryIR:
    return _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=start_date, end=end_date),
        filters=filters,
    )


def _accounts_info() -> list[dict[str, Any]]:
    return [{"id": "linked-1", "account_id": "acc_1", "bank_name": "First Bank"}]


def _mirrored_row(
    *,
    linked_account_id: str,
    provider_transaction_id: str,
    posted_at: datetime,
    amount: float,
    transaction_type: str,
    narration: str,
    category: str | None = None,
    counterparty: str | None = None,
    counterparty_role: str | None = None,
) -> Any:
    return SimpleNamespace(
        linked_account_id=linked_account_id,
        provider_transaction_id=provider_transaction_id,
        posted_at=posted_at,
        posted_date=posted_at.date(),
        amount=amount,
        currency="NGN",
        transaction_type=transaction_type,
        narration=narration,
        category=category,
        counterparty=counterparty,
        counterparty_role=counterparty_role,
        counterparty_source="narration" if counterparty else None,
        resolved_category=category,
        category_source="provider" if category else None,
        parser_rule="test_fixture",
        bank_name="First Bank",
        first_seen_at=posted_at,
        last_seen_at=posted_at,
    )


@pytest.mark.asyncio
async def test_fully_covered_historical_query_reads_from_mirror_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _MirrorState()
    historical_day = date(2026, 1, 10)
    state.coverage["linked-1"] = [(historical_day, historical_day)]
    state.accounts["acc_1"] = SimpleNamespace(account_id="acc_1", extra_data={})
    state.bank_transactions[("linked-1", "txn_1")] = _mirrored_row(
        linked_account_id="linked-1",
        provider_transaction_id="txn_1",
        posted_at=datetime(2026, 1, 10, 10, 0),
        amount=950000,
        transaction_type="credit",
        narration="Salary",
    )
    monkeypatch.setattr("banking.persistence.unit_of_work.UnitOfWork", lambda: _FakeUnitOfWork(state))
    provider = _PagedProvider({})

    result = await fetch_transactions_base(
        provider,  # type: ignore[arg-type]
        _query(start_date=historical_day, end_date=historical_day),
        account_id="acc_1",
        account_ids=["acc_1"],
        accounts_info=_accounts_info(),
        user_id="user-1",
    )

    assert provider.calls == []
    assert [item["id"] for item in result] == ["txn_1"]
    assert result[0]["source_account_id"] == "acc_1"
    assert result[0]["source_account_label"] == "First Bank"


@pytest.mark.asyncio
async def test_uncovered_historical_query_gap_fills_once_then_reuses_mirror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _MirrorState()
    state.accounts["acc_1"] = SimpleNamespace(account_id="acc_1", extra_data={})
    query_day = date(2026, 1, 10)
    provider = _PagedProvider(
        {
            (
                "acc_1",
                query_day.isoformat(),
                query_day.isoformat(),
                1,
            ): [
                TransactionData(
                    transaction_id="txn_1",
                    date="2026-01-10T12:00:00.000Z",
                    narration="Salary",
                    amount=950000,
                    transaction_type="credit",
                    category="income",
                )
            ]
        }
    )
    monkeypatch.setattr("banking.persistence.unit_of_work.UnitOfWork", lambda: _FakeUnitOfWork(state))

    first = await fetch_transactions_base(
        provider,  # type: ignore[arg-type]
        _query(start_date=query_day, end_date=query_day),
        account_id="acc_1",
        account_ids=["acc_1"],
        accounts_info=_accounts_info(),
        user_id="user-1",
    )
    second = await fetch_transactions_base(
        provider,  # type: ignore[arg-type]
        _query(start_date=query_day, end_date=query_day),
        account_id="acc_1",
        account_ids=["acc_1"],
        accounts_info=_accounts_info(),
        user_id="user-1",
    )

    assert len(provider.calls) == 1
    assert [item["id"] for item in first] == ["txn_1"]
    assert [item["id"] for item in second] == ["txn_1"]
    assert state.coverage["linked-1"] == [(query_day, query_day)]


@pytest.mark.asyncio
async def test_recent_overlap_sync_captures_same_day_late_transactions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _MirrorState()
    today = date(2026, 3, 24)
    state.coverage["linked-1"] = [(today, today)]
    state.accounts["acc_1"] = SimpleNamespace(account_id="acc_1", extra_data={})
    state.bank_transactions[("linked-1", "txn_early")] = _mirrored_row(
        linked_account_id="linked-1",
        provider_transaction_id="txn_early",
        posted_at=datetime(2026, 3, 24, 12, 0),
        amount=10000,
        transaction_type="debit",
        narration="Netflix Monthly Subscription",
        category="entertainment",
    )
    provider = _PagedProvider(
        {
            (
                "acc_1",
                today.isoformat(),
                today.isoformat(),
                1,
            ): [
                TransactionData(
                    transaction_id="txn_early",
                    date="2026-03-24T12:00:00.000Z",
                    narration="Netflix Monthly Subscription",
                    amount=-10000,
                    transaction_type="debit",
                    category="entertainment",
                ),
                TransactionData(
                    transaction_id="txn_late",
                    date="2026-03-24T18:00:00.000Z",
                    narration="Transfer from Ada",
                    amount=500000,
                    transaction_type="credit",
                    category="transfer",
                ),
            ]
        }
    )
    monkeypatch.setattr("banking.persistence.unit_of_work.UnitOfWork", lambda: _FakeUnitOfWork(state))
    monkeypatch.setattr("apps.chat.src.agent.workers.query.services.fetching.bank_transaction_mirror.lagos_today", lambda: today)

    result = await fetch_transactions_base(
        provider,  # type: ignore[arg-type]
        _query(start_date=today, end_date=today),
        account_id="acc_1",
        account_ids=["acc_1"],
        accounts_info=_accounts_info(),
        user_id="user-1",
    )

    assert len(provider.calls) == 1
    assert [item["id"] for item in result] == ["txn_late", "txn_early"]


@pytest.mark.asyncio
async def test_local_filters_apply_on_mirrored_transactions_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _MirrorState()
    historical_day = date(2026, 1, 10)
    state.coverage["linked-1"] = [(historical_day, historical_day)]
    state.accounts["acc_1"] = SimpleNamespace(account_id="acc_1", extra_data={})
    state.bank_transactions[("linked-1", "txn_1")] = _mirrored_row(
        linked_account_id="linked-1",
        provider_transaction_id="txn_1",
        posted_at=datetime(2026, 1, 10, 10, 0),
        amount=950000,
        transaction_type="credit",
        narration="Salary from Acme Corp",
        category="income",
    )
    state.bank_transactions[("linked-1", "txn_2")] = _mirrored_row(
        linked_account_id="linked-1",
        provider_transaction_id="txn_2",
        posted_at=datetime(2026, 1, 10, 12, 0),
        amount=-5000,
        transaction_type="debit",
        narration="Netflix Monthly Subscription",
        category="entertainment",
    )
    monkeypatch.setattr("banking.persistence.unit_of_work.UnitOfWork", lambda: _FakeUnitOfWork(state))
    provider = _PagedProvider({})

    result = await fetch_and_filter(
        provider,  # type: ignore[arg-type]
        _query(
            start_date=historical_day,
            end_date=historical_day,
            filters=Filters(transaction_type="credit", merchant=["salary"]),
        ),
        account_id="acc_1",
        account_ids=["acc_1"],
        accounts_info=_accounts_info(),
        user_id="user-1",
    )

    assert provider.calls == []
    assert [item["id"] for item in result] == ["txn_1"]


@pytest.mark.asyncio
async def test_counterparty_filter_uses_parsed_mirror_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _MirrorState()
    historical_day = date(2026, 1, 10)
    state.coverage["linked-1"] = [(historical_day, historical_day)]
    state.accounts["acc_1"] = SimpleNamespace(account_id="acc_1", extra_data={})
    state.bank_transactions[("linked-1", "txn_1")] = _mirrored_row(
        linked_account_id="linked-1",
        provider_transaction_id="txn_1",
        posted_at=datetime(2026, 1, 10, 10, 0),
        amount=950000,
        transaction_type="credit",
        narration="Transfer from JOHNSON MARY - Refund",
        category="transfer",
        counterparty="Johnson Mary",
        counterparty_role="sender",
    )
    monkeypatch.setattr("banking.persistence.unit_of_work.UnitOfWork", lambda: _FakeUnitOfWork(state))
    provider = _PagedProvider({})

    result = await fetch_and_filter(
        provider,  # type: ignore[arg-type]
        _query(
            start_date=historical_day,
            end_date=historical_day,
            filters=Filters(transaction_type="credit", counterparty=["johnson"]),
        ),
        account_id="acc_1",
        account_ids=["acc_1"],
        accounts_info=_accounts_info(),
        user_id="user-1",
    )

    assert provider.calls == []
    assert [item["id"] for item in result] == ["txn_1"]


@pytest.mark.asyncio
async def test_category_filter_uses_provider_category_from_mirror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _MirrorState()
    historical_day = date(2026, 1, 10)
    state.coverage["linked-1"] = [(historical_day, historical_day)]
    state.accounts["acc_1"] = SimpleNamespace(account_id="acc_1", extra_data={})
    state.bank_transactions[("linked-1", "txn_1")] = _mirrored_row(
        linked_account_id="linked-1",
        provider_transaction_id="txn_1",
        posted_at=datetime(2026, 1, 10, 10, 0),
        amount=-8000,
        transaction_type="debit",
        narration="POS PURCHASE 000123",
        category="food",
    )
    monkeypatch.setattr("banking.persistence.unit_of_work.UnitOfWork", lambda: _FakeUnitOfWork(state))
    provider = _PagedProvider({})

    result = await fetch_and_filter(
        provider,  # type: ignore[arg-type]
        _query(
            start_date=historical_day,
            end_date=historical_day,
            filters=Filters(transaction_type="debit", category=["food"]),
        ),
        account_id="acc_1",
        account_ids=["acc_1"],
        accounts_info=_accounts_info(),
        user_id="user-1",
    )

    assert provider.calls == []
    assert [item["id"] for item in result] == ["txn_1"]


@pytest.mark.asyncio
async def test_category_breakdown_uses_provider_or_resolved_category() -> None:
    query = _query_ir(
        intent=QueryIntent.ANALYTICS_SUMMARY,
        time_range=TimeRange(start=date(2026, 1, 10), end=date(2026, 1, 10)),
        aggregation=Aggregation(type="breakdown", group_by="category"),
    )

    result = await _aggregate_breakdown(
        [
            {
                "id": "txn_1",
                "date": "2026-01-10T10:00:00.000Z",
                "amount": -8000,
                "type": "debit",
                "narration": "POS PURCHASE 000123",
                "category": "food and drink",
            }
        ],
        query,
        language="en",
    )

    assert result.items is not None
    assert result.items[0].description == "food"


@pytest.mark.asyncio
async def test_gap_fill_persists_parsed_counterparty_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _MirrorState()
    state.accounts["acc_1"] = SimpleNamespace(account_id="acc_1", extra_data={})
    query_day = date(2026, 1, 10)
    provider = _PagedProvider(
        {
            (
                "acc_1",
                query_day.isoformat(),
                query_day.isoformat(),
                1,
            ): [
                TransactionData(
                    transaction_id="txn_1",
                    date="2026-01-10T12:00:00.000Z",
                    narration="Transfer from JOHNSON MARY - Refund",
                    amount=950000,
                    transaction_type="credit",
                    category="transfer",
                )
            ]
        }
    )
    monkeypatch.setattr("banking.persistence.unit_of_work.UnitOfWork", lambda: _FakeUnitOfWork(state))

    result = await fetch_transactions_base(
        provider,  # type: ignore[arg-type]
        _query(start_date=query_day, end_date=query_day),
        account_id="acc_1",
        account_ids=["acc_1"],
        accounts_info=_accounts_info(),
        user_id="user-1",
    )

    assert result[0]["counterparty"] == "Johnson Mary"
    stored_row = state.bank_transactions[("linked-1", "txn_1")]
    assert stored_row.counterparty == "Johnson Mary"
    assert stored_row.counterparty_role == "sender"
    assert stored_row.parser_rule == "transfer:transfer_from"
