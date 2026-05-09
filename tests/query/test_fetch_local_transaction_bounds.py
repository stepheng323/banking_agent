from datetime import date
from typing import Any

import pytest

from apps.chat.src.agent.graphs.query.handlers.transactions import handle_transaction_list
from apps.chat.src.agent.graphs.query.models import (
    Filters,
    QueryExecutionContract,
    QueryIntent,
    QueryIR,
    TimeRange,
)
from apps.chat.src.agent.graphs.query.services.formatter import QueryFormatter


def _query_ir(**kwargs: object) -> QueryIR:
    fallback_day = date(2026, 3, 6)
    defaults: dict[str, object] = {
        "intent": QueryIntent.TRANSACTION_LIST,
        "time_range": TimeRange(start=fallback_day, end=fallback_day),
    }
    defaults.update(kwargs)
    return QueryIR(**defaults)


class _Provider:
    async def get_transactions(
        self,
        account_id: str,
        start_date: str,
        end_date: str,
        limit: int = 100,
        user_id: str | None = None,
        mock_account_slot: int | None = None,
    ) -> list[dict[str, Any]]:
        del account_id, start_date, end_date, limit, user_id, mock_account_slot
        return []


def _query_for_today(today: date) -> QueryIR:
    return _query_ir(
        intent=QueryIntent.TRANSACTION_LIST,
        time_range=TimeRange(start=today, end=today),
        filters=Filters(transaction_type="debit"),
    )


def _contract(query: QueryIR) -> QueryExecutionContract:
    return QueryExecutionContract.from_query_ir(query)


@pytest.mark.asyncio
async def test_query_results_are_bank_feed_only_when_provider_returns_no_transactions() -> None:
    query_day = date(2026, 3, 6)

    result = await handle_transaction_list(
        _Provider(),  # type: ignore[arg-type]
        _contract(_query_for_today(query_day)),
        account_id="acc_1",
        account_ids=["acc_1"],
        user_id="user_1",
        language="en",
    )

    assert result.items == []


@pytest.mark.asyncio
async def test_today_query_with_no_bank_feed_rows_returns_no_results_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query_day = date(2026, 3, 6)
    monkeypatch.setattr("apps.chat.src.agent.graphs.query.services.contracts.lagos_today", lambda: query_day)

    query = _query_for_today(query_day)
    result = await handle_transaction_list(
        _Provider(),  # type: ignore[arg-type]
        _contract(query),
        account_id="acc_1",
        account_ids=["acc_1"],
        user_id="user_1",
        language="en",
    )
    result.query_contract = _contract(query)

    assert QueryFormatter.format(result, locale="en") == "You had no debit transactions today."
