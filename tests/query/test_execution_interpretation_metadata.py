from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from apps.core.src.agent.graphs.query.models import (
    Aggregation,
    ComparisonDirective,
    Filters,
    NormalizedQuery,
    QueryExecutionContract,
    QueryIntent,
    QueryResult,
    TimeRange,
)
from apps.core.src.agent.graphs.query.nodes.execution import ExecutionStep
from apps.core.src.agent.graphs.query.utils.timezone import lagos_today
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome


@pytest.mark.asyncio
async def test_execution_populates_interpretation_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    query_contract = QueryExecutionContract.from_normalized_query(
        NormalizedQuery(
            intent=QueryIntent.TIME_COMPARISON,
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 7), granularity="day"),
            filters=Filters(transaction_type="debit", min_amount=1000),
            aggregation=Aggregation(type="sum"),
            result_limit=5,
            result_reference="latest",
        ),
        comparison=ComparisonDirective(
            mode="explicit_range",
            explicit_range=TimeRange(start=date(2026, 2, 1), end=date(2026, 2, 7), granularity="day"),
        ),
        continuation_type="time_delta",
        continuation_delta_type="time",
    )

    async def _fake_execute(self, **kwargs):  # type: ignore[no-untyped-def]
        del self, kwargs
        return QueryResult(summary_text="comparison done")

    monkeypatch.setattr("apps.core.src.agent.graphs.query.nodes.execution.QueryExecutor.execute", _fake_execute)

    step = ExecutionStep()
    result = await step.run(
        state={
            "flow_state": "executing",
            "language": "en",
            "query_contract": query_contract,
            "account_id": "acc_1",
            "account_ids": ["acc_1"],
            "accounts": [],
            "query_session": {},
            "current_page": 0,
            "page_size": 5,
            "show_expanded": False,
        },
        worker_context=SimpleNamespace(banking_provider=object(), user_id="u1"),
    )

    assert result.outcome == TransactionOutcome.OK
    query_result = result.patch["query_result"]
    interpretation = query_result.interpretation
    assert interpretation is not None
    assert interpretation["intent"] == "time_comparison"
    assert interpretation["time_window"]["start"] == "2026-03-01"
    assert interpretation["time_window"]["end"] == "2026-03-07"
    assert interpretation["comparison"] == {
        "mode": "explicit_range",
        "start": "2026-02-01",
        "end": "2026-02-07",
    }
    assert interpretation["filters"]["transaction_type"] == "debit"
    assert interpretation["aggregation"]["type"] == "sum"
    assert interpretation["result_limit"] == 5
    assert interpretation["result_reference"] == "latest"
    assert interpretation["continuation_type"] == "time_delta"
    assert interpretation["continuation_delta_type"] == "time"


@pytest.mark.asyncio
async def test_execution_formats_time_scoped_single_transaction_no_results_as_direct_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    yesterday = lagos_today() - timedelta(days=1)
    query_contract = QueryExecutionContract.from_normalized_query(
        NormalizedQuery(
            intent=QueryIntent.TRANSACTION_SEARCH,
            time_range=TimeRange(start=yesterday, end=yesterday),
            result_limit=1,
            result_reference="latest",
        ),
        continuation_type="time_delta",
    )

    async def _fake_execute(self, **kwargs):  # type: ignore[no-untyped-def]
        del self, kwargs
        return QueryResult(summary_text="", items=[], query_snapshot=query_contract.normalized_query)

    monkeypatch.setattr("apps.core.src.agent.graphs.query.nodes.execution.QueryExecutor.execute", _fake_execute)

    step = ExecutionStep()
    result = await step.run(
        state={
            "flow_state": "executing",
            "language": "en",
            "query_contract": query_contract,
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
