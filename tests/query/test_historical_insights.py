from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from banking.transactions.query.insights.anomalies import execute_anomalies
from banking.transactions.query.insights.counterparty_concentration import execute_counterparty_concentration
from banking.transactions.query.insights.recurring_patterns import execute_recurring_patterns
from banking.transactions.query.models.operations import (
    AnalyzeOperation,
    AnomaliesSpec,
    CounterpartyConcentrationSpec,
    QueryRequest,
    QueryScope,
    RecurringPatternsSpec,
    ResolvedPeriod,
)
from banking.transactions.query.services.analysis.kernel.contracts import AnalysisDataset, CoverageStatus


class MockAnalysisService:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows

    async def load_dataset(self, *args, **kwargs) -> AnalysisDataset:
        return AnalysisDataset(
            basis="ledger_transactions",
            period_label="current",
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            coverage_status=CoverageStatus.COMPLETE,
            rows=self.rows,
        )


def _build_contract(analysis_spec: Any) -> QueryRequest:
    return QueryRequest(
        operation=AnalyzeOperation(
            scope=QueryScope(period=ResolvedPeriod(start=date(2023, 1, 1), end=date(2023, 12, 31))),
            analysis=analysis_spec,
        )
    )


@pytest.mark.asyncio
async def test_recurring_patterns() -> None:
    # 3 monthly transactions for Netflix
    rows = [
        {
            "transaction_id": "1",
            "date": "2023-01-01T10:00:00Z",
            "amount": 15.0,
            "status": "posted",
            "type": "debit",
            "counterparty": "Netflix",
            "source_account_id": "account-1",
        },
        {
            "transaction_id": "2",
            "date": "2023-01-31T10:00:00Z",
            "amount": 15.0,
            "status": "posted",
            "type": "debit",
            "counterparty": "Netflix",
            "source_account_id": "account-1",
        },
        {
            "transaction_id": "3",
            "date": "2023-03-02T10:00:00Z",
            "amount": 15.0,
            "status": "posted",
            "type": "debit",
            "counterparty": "Netflix",
            "source_account_id": "account-1",
        },
        # 1 random transaction
        {
            "transaction_id": "4",
            "date": "2023-03-05T10:00:00Z",
            "amount": 100.0,
            "status": "posted",
            "type": "debit",
            "counterparty": "Grocery",
        },
    ]
    service = MockAnalysisService(rows)
    contract = _build_contract(RecurringPatternsSpec())

    result = await execute_recurring_patterns(service, contract, "acc1", ["acc1"], None, user_id="u1")

    assert result.available is True
    assert len(result.series) == 1
    series = result.series[0]
    assert series.counterparty == "Netflix"
    assert series.frequency == "monthly"
    assert series.average_amount == Decimal("15.0")
    assert series.transaction_count == 3


@pytest.mark.asyncio
async def test_anomalies() -> None:
    rows = []
    # 6 normal transactions for groceries
    for i in range(1, 7):
        rows.append(
            {
                "transaction_id": f"g_{i}",
                "date": f"2023-01-0{i}T10:00:00Z",
                "amount": 50_000.0,
                "status": "posted",
                "type": "debit",
                "category": "Groceries",
            }
        )
    # 1 anomalous transaction for groceries
    rows.append(
        {
            "transaction_id": "g_anom",
            "date": "2023-01-10T10:00:00Z",
            "amount": 250_000.0,
            "status": "posted",
            "type": "debit",
            "category": "Groceries",
        }
    )

    # 1 small transaction that shouldn't be flagged even if > 3x average because average is too small
    rows.append(
        {
            "transaction_id": "s_1",
            "date": "2023-01-01T10:00:00Z",
            "amount": 1.0,
            "status": "posted",
            "category": "Small",
        }
    )
    rows.append(
        {
            "transaction_id": "s_2",
            "date": "2023-01-01T10:00:00Z",
            "amount": 1.0,
            "status": "posted",
            "category": "Small",
        }
    )
    rows.append(
        {
            "transaction_id": "s_3",
            "date": "2023-01-01T10:00:00Z",
            "amount": 1.0,
            "status": "posted",
            "category": "Small",
        }
    )
    rows.append(
        {
            "transaction_id": "s_4",
            "date": "2023-01-01T10:00:00Z",
            "amount": 1.0,
            "status": "posted",
            "category": "Small",
        }
    )
    rows.append(
        {
            "transaction_id": "s_5",
            "date": "2023-01-01T10:00:00Z",
            "amount": 1.0,
            "status": "posted",
            "category": "Small",
        }
    )
    rows.append(
        {
            "transaction_id": "s_6",
            "date": "2023-01-01T10:00:00Z",
            "amount": 1.0,
            "status": "posted",
            "category": "Small",
        }
    )
    rows.append(
        {
            "transaction_id": "s_anom",
            "date": "2023-01-10T10:00:00Z",
            "amount": 5.0,
            "status": "posted",
            "category": "Small",
        }
    )  # > 3x average, but avg < $20

    service = MockAnalysisService(rows)
    contract = _build_contract(AnomaliesSpec())

    result = await execute_anomalies(service, contract, "acc1", ["acc1"], None, user_id="u1")

    assert result.available is True
    assert len(result.anomalies) == 1
    anom = result.anomalies[0]
    assert anom.dimension_name == "category"
    assert anom.dimension_value == "groceries"
    assert float(anom.multiplier) > 3.0


@pytest.mark.asyncio
async def test_counterparty_concentration() -> None:
    rows = [
        {
            "transaction_id": "1",
            "date": "2023-01-01T10:00:00Z",
            "amount": 800.0,
            "status": "posted",
            "type": "debit",
            "counterparty": "Amazon",
        },
        {
            "transaction_id": "2",
            "date": "2023-01-02T10:00:00Z",
            "amount": 100.0,
            "status": "posted",
            "type": "debit",
            "counterparty": "Uber",
        },
        {
            "transaction_id": "3",
            "date": "2023-01-03T10:00:00Z",
            "amount": 100.0,
            "status": "posted",
            "type": "debit",
            "counterparty": "Netflix",
        },
    ]
    service = MockAnalysisService(rows)
    contract = _build_contract(CounterpartyConcentrationSpec(measure="spending"))

    result = await execute_counterparty_concentration(service, contract, "acc1", ["acc1"], None, user_id="u1")

    assert result.available is True
    assert len(result.groups) == 3
    assert result.groups[0].counterparty == "Amazon"
    assert result.groups[0].percentage == Decimal("80.0")
    assert result.groups[1].counterparty == "Uber"
    assert result.groups[1].percentage == Decimal("10.0")
