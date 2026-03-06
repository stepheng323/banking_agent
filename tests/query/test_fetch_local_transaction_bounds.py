from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from apps.core.src.agent.graphs.query.handlers.transactions import handle_transaction_list
from apps.core.src.agent.graphs.query.models import Filters, NormalizedQuery, QueryIntent, TimeRange
from apps.core.src.agent.graphs.query.services.formatter import QueryFormatter


class _Provider:
    async def get_transactions(
        self,
        account_id: str,
        start_date: str,
        end_date: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        del account_id, start_date, end_date, limit
        return []


class _TxRepo:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def get_by_user(self, user_id: str, limit: int = 20) -> list[Any]:
        del user_id, limit
        return list(self._rows)


class _UnitOfWorkStub:
    def __init__(self, rows: list[Any]) -> None:
        self.transactions = _TxRepo(rows)

    async def __aenter__(self) -> "_UnitOfWorkStub":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        del exc_type, exc, tb
        return None


def _local_row(*, id_: str, created_at: datetime, amount: float, narration: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=id_,
        created_at=created_at,
        amount=amount,
        transaction_type="transfer",
        narration=narration,
        currency="NGN",
        status="successful",
        transaction_id=f"tx-{id_}",
        recipient_name="Tolu",
        recipient_account_number="0123456789",
        recipient_bank_name="Access",
        recipient_bank_code="044",
        source_bank_name="Zenith",
    )


def _query_for_today(today: date) -> NormalizedQuery:
    return NormalizedQuery(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today, end=today),
        filters=Filters(transaction_type="debit"),
    )


@pytest.mark.asyncio
async def test_local_rows_are_filtered_by_lagos_date_window(monkeypatch: pytest.MonkeyPatch) -> None:
    query_day = date(2026, 3, 6)
    rows = [
        _local_row(id_="in", created_at=datetime(2026, 3, 5, 23, 30), amount=10_000, narration="Payment to Tolu"),
        _local_row(id_="out", created_at=datetime(2026, 3, 5, 10, 0), amount=5_000, narration="Payment to Mum"),
    ]
    monkeypatch.setattr(
        "shared.repositories.unit_of_work.UnitOfWork",
        lambda: _UnitOfWorkStub(rows),
    )

    result = await handle_transaction_list(
        _Provider(),  # type: ignore[arg-type]
        _query_for_today(query_day),
        account_id="acc_1",
        account_ids=["acc_1"],
        user_id="user_1",
        language="en",
    )

    assert result.items is not None
    assert len(result.items) == 1
    assert result.items[0].date == query_day
    assert "Tolu" in result.items[0].description


@pytest.mark.asyncio
async def test_today_query_with_only_out_of_window_local_rows_returns_no_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query_day = date(2026, 3, 6)
    rows = [
        _local_row(id_="out", created_at=datetime(2026, 3, 5, 10, 0), amount=5_000, narration="Payment to Mum"),
    ]
    monkeypatch.setattr(
        "shared.repositories.unit_of_work.UnitOfWork",
        lambda: _UnitOfWorkStub(rows),
    )

    query = _query_for_today(query_day)
    result = await handle_transaction_list(
        _Provider(),  # type: ignore[arg-type]
        query,
        account_id="acc_1",
        account_ids=["acc_1"],
        user_id="user_1",
        language="en",
    )
    result.query_snapshot = query

    assert QueryFormatter.format(result, locale="en") == "No debit transactions found today."

