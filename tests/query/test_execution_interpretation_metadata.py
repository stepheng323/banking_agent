from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from banking.runtime.results import TransactionOutcome
from banking.transactions.query.models.domain import QueryResult
from banking.transactions.query.models.operations import (
    AmountRange,
    ExplicitBaseline,
    Money,
    ResolvedPeriod,
    RetrieveProjection,
    RetrieveSelection,
    TransactionPredicate,
)
from banking.transactions.query.nodes.execution import ExecutionStep
from banking.transactions.query.utils.timezone import lagos_today
from tests.query.factories import compare_request, query_scope, retrieve_request


@pytest.mark.asyncio
async def test_execution_populates_interpretation_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    query_request = compare_request(
        query_scope(
            date(2026, 3, 1),
            date(2026, 3, 7),
            granularity="day",
            predicate=TransactionPredicate(
                direction="debit", amount=AmountRange(minimum=Money(amount=1000))
            ),
        ),
        baseline=ExplicitBaseline(
            period=ResolvedPeriod(start=date(2026, 2, 1), end=date(2026, 2, 7), granularity="day")
        ),
    )

    async def _fake_execute(self, **kwargs):  # type: ignore[no-untyped-def]
        del self, kwargs
        return QueryResult(summary_text="comparison done")

    monkeypatch.setattr("banking.transactions.query.nodes.execution.QueryExecutor.execute", _fake_execute)

    step = ExecutionStep()
    result = await step.run(
        state={
            "flow_state": "executing",
            "language": "en",
            "query_request": query_request,
            "account_id": "acc_1",
            "account_ids": ["acc_1"],
            "accounts": [],
            "query_session": {},
            "current_page": 0,
            "page_size": 5,
            "show_expanded": False,
            "continuation_type": "time_delta",
            "continuation_delta_type": "time",
        },
        worker_context=SimpleNamespace(banking_provider=object(), user_id="u1"),
    )

    assert result.outcome == TransactionOutcome.OK
    query_result = result.patch["query_result"]
    interpretation = query_result.interpretation
    assert interpretation is not None
    operation = interpretation["operation"]
    assert operation["kind"] == "compare"
    assert operation["scope"]["period"]["start"] == "2026-03-01"
    assert operation["scope"]["period"]["end"] == "2026-03-07"
    assert operation["comparison"]["baseline"]["type"] == "explicit"
    assert operation["comparison"]["baseline"]["period"]["start"] == "2026-02-01"
    assert operation["scope"]["predicate"]["direction"] == "debit"
    assert interpretation["continuation_type"] == "time_delta"
    assert interpretation["continuation_delta_type"] == "time"


@pytest.mark.asyncio
async def test_execution_formats_time_scoped_single_transaction_no_results_as_direct_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    yesterday = lagos_today() - timedelta(days=1)
    query_request = retrieve_request(
        query_scope(yesterday, yesterday),
        projection=RetrieveProjection(shape="detail"),
        selection=RetrieveSelection(cardinality="one", order="latest", order_explicit=True, limit=1),
    )

    async def _fake_execute(self, **kwargs):  # type: ignore[no-untyped-def]
        del self, kwargs
        return QueryResult(summary_text="", items=[], query_request=query_request)

    monkeypatch.setattr("banking.transactions.query.nodes.execution.QueryExecutor.execute", _fake_execute)

    step = ExecutionStep()
    result = await step.run(
        state={
            "flow_state": "executing",
            "language": "en",
            "query_request": query_request,
            "account_id": "acc_1",
            "account_ids": ["acc_1"],
            "accounts": [],
            "query_session": {},
            "current_page": 0,
            "page_size": 5,
            "show_expanded": False,
            "continuation_type": "time_delta",
        },
        worker_context=SimpleNamespace(banking_provider=object(), user_id="u1"),
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.response == "You had no transactions yesterday."
