from datetime import UTC, datetime
from decimal import Decimal

from banking.transactions.query.insights.cash_flow_quality import execute_cash_flow_quality
from banking.transactions.query.insights.forecast import execute_forecast
from banking.transactions.query.insights.runway import execute_runway, format_runway
from banking.transactions.query.models.domain import QueryRequest
from banking.transactions.query.models.operations import (
    AnalyzeOperation,
    CashFlowQualitySpec,
    ForecastSpec,
    QueryScope,
    ResolvedPeriod,
    RunwaySpec,
)
from banking.transactions.query.services.analysis.kernel.contracts import AnalysisDataset, CoverageStatus
from shared.clients.abstractions.banking import BalanceData


class MockProvider:
    def __init__(self, balances=None):
        self.balances = balances or {}

    async def get_balance(self, account_id, real_time=True):
        value = self.balances.get(account_id)
        return BalanceData(available_balance=Decimal(str(value)), account_id=account_id) if value is not None else None


class MockAnalysisService:
    def __init__(self, dataset, balances=None):
        self.dataset = dataset
        self.called_period_label = None
        self.provider = MockProvider(balances)

    async def load_dataset(self, contract, account_id, account_ids, accounts_info, *, user_id, basis, period_label):
        self.called_period_label = period_label
        return self.dataset


def create_mock_tx(amount, direction="debit", date_str="2023-10-15T12:00:00Z", status="successful", event_type="transfer"):
    return {
        "amount": amount,
        "type": direction,
        "date": date_str,
        "status": status,
        "event_type": event_type,
    }


def test_forecast_calculation():
    # A complete 30-day observation window.
    txs = [
        create_mock_tx(300, date_str="2023-10-01T12:00:00Z"),
        create_mock_tx(150, date_str="2023-10-02T12:00:00Z"),
        create_mock_tx(450, date_str="2023-10-03T12:00:00Z"),
        create_mock_tx(100, direction="credit"),  # Ignored
        create_mock_tx(50, event_type="fee"),  # Ignored
        create_mock_tx(200, status="failed"),  # Ignored
    ]

    start_date = datetime(2023, 10, 1, tzinfo=UTC)
    end_date = datetime(2023, 10, 30, tzinfo=UTC)

    dataset = AnalysisDataset(
        rows=txs,
        start_date=start_date,
        end_date=end_date,
        is_partial=False,
        basis="economic_events",
        period_label="history",
        coverage_status=CoverageStatus.COMPLETE,
    )

    service = MockAnalysisService(dataset)

    req = QueryRequest(
        operation=AnalyzeOperation(
            scope=QueryScope(period=ResolvedPeriod(start=start_date, end=end_date)),
            analysis=ForecastSpec(history_days=30, horizon_days=10)
        )
    )

    import asyncio
    result = asyncio.run(execute_forecast(
        service, req, "acc_1", ["acc_1"], [{"account_id": "acc_1", "available_balance": 5000}], user_id="u1"
    ))

    assert result.available
    # Total valid debit = 300 + 150 + 450 = 900
    # avg = 900 / 30 = 30
    assert result.average_daily_spend == Decimal("30")
    assert result.projected_spend == Decimal("300")


def test_runway_calculation():
    txs = [
        create_mock_tx(200, date_str="2023-10-01T12:00:00Z"),
        create_mock_tx(800, date_str="2023-10-03T12:00:00Z"),
    ]

    start_date = datetime(2023, 10, 1, tzinfo=UTC)
    end_date = datetime(2023, 10, 30, tzinfo=UTC)

    dataset = AnalysisDataset(
        rows=txs,
        start_date=start_date,
        end_date=end_date,
        is_partial=False,
        basis="economic_events",
        period_label="history",
        coverage_status=CoverageStatus.COMPLETE,
    )

    service = MockAnalysisService(dataset, balances={"acc_1": 3000, "acc_2": 2000})

    req = QueryRequest(
        operation=AnalyzeOperation(
            scope=QueryScope(period=ResolvedPeriod(start=start_date, end=end_date)),
            analysis=RunwaySpec(baseline_days=30)
        )
    )

    import asyncio
    result = asyncio.run(execute_runway(
        service, req, "acc_1", ["acc_1", "acc_2"], None, user_id="u1"
    ))

    assert result.available
    # Total spend = 1000, days = 30; balance = 5000 across both accounts.
    assert result.average_burn_rate == Decimal("1000") / Decimal("30")
    assert result.runway_days == 150


def test_cash_flow_quality():
    txs = [
        # Month 1
        create_mock_tx(1000, direction="credit", date_str="2023-10-01T12:00:00Z"),
        create_mock_tx(200, direction="debit", date_str="2023-10-15T12:00:00Z"),
        # Month 2
        create_mock_tx(1500, direction="credit", date_str="2023-11-01T12:00:00Z"),
        create_mock_tx(1000, direction="debit", date_str="2023-11-15T12:00:00Z"),
        # Month 3
        create_mock_tx(500, direction="credit", date_str="2023-12-01T12:00:00Z"),
        create_mock_tx(300, direction="debit", date_str="2023-12-15T12:00:00Z"),
    ]

    start_date = datetime(2023, 10, 1, tzinfo=UTC)
    end_date = datetime(2023, 12, 31, tzinfo=UTC)

    dataset = AnalysisDataset(
        rows=txs,
        start_date=start_date,
        end_date=end_date,
        is_partial=False,
        basis="economic_events",
        period_label="history",
        coverage_status=CoverageStatus.COMPLETE,
    )

    service = MockAnalysisService(dataset)

    req = QueryRequest(
        operation=AnalyzeOperation(
            scope=QueryScope(
                period=ResolvedPeriod(
                    start=datetime(2023, 10, 1, tzinfo=UTC),
                    end=datetime(2024, 1, 15, tzinfo=UTC),
                )
            ),
            analysis=CashFlowQualitySpec(min_complete_months=3)
        )
    )

    import asyncio
    result = asyncio.run(execute_cash_flow_quality(
        service, req, "acc_1", ["acc_1"], [{"account_id": "acc_1", "available_balance": "5000"}], user_id="u1"
    ))
    assert result.available
    assert result.analyzed_months == 3

    # Total Inflow = 1000 + 1500 + 500 = 3000
    # Total Outflow = 200 + 1000 + 300 = 1500
    # Ratio = 3000 / 1500 = 2.0
    assert result.inflow_outflow_ratio == Decimal("2.0")
    assert result.is_healthy is True


def test_runway_incomplete_coverage_copy_names_the_estimate() -> None:
    from banking.transactions.query.services.analysis.kernel.contracts import RunwayResult

    response = format_runway(
        RunwayResult(available=False, unavailable_reason="coverage_incomplete"),
        "en",
    )

    assert "cash runway" in response
    assert "not fully synchronized" in response


def test_forecast_uses_actual_covered_days_not_requested_dates() -> None:
    start_date = datetime(2023, 10, 1, tzinfo=UTC)
    end_date = datetime(2023, 10, 30, tzinfo=UTC)
    dataset = AnalysisDataset(
        rows=[create_mock_tx(300)],
        start_date=start_date,
        end_date=end_date,
        basis="economic_events",
        period_label="history",
        coverage_status=CoverageStatus.PARTIAL,
        requested_days=30,
        fully_covered_days=29,
    )
    request = QueryRequest(
        operation=AnalyzeOperation(
            scope=QueryScope(period=ResolvedPeriod(start=start_date, end=end_date)),
            analysis=ForecastSpec(history_days=30, horizon_days=10),
        )
    )

    import asyncio

    result = asyncio.run(execute_forecast(MockAnalysisService(dataset), request, "acc", ["acc"], None, user_id="u"))

    assert result.available is False
    assert result.unavailable_reason == "insufficient_history"


def test_cash_flow_quality_never_labels_partial_calendar_months_healthy() -> None:
    start_date = datetime(2023, 10, 1, tzinfo=UTC)
    end_date = datetime(2023, 12, 31, tzinfo=UTC)
    dataset = AnalysisDataset(
        rows=[create_mock_tx(1000, direction="credit"), create_mock_tx(100, direction="debit")],
        start_date=start_date,
        end_date=end_date,
        basis="economic_events",
        period_label="history",
        coverage_status=CoverageStatus.PARTIAL,
        requested_days=92,
        fully_covered_days=60,
    )
    request = QueryRequest(
        operation=AnalyzeOperation(
            scope=QueryScope(period=ResolvedPeriod(start=start_date, end=datetime(2024, 1, 15, tzinfo=UTC))),
            analysis=CashFlowQualitySpec(min_complete_months=3),
        )
    )

    import asyncio

    result = asyncio.run(execute_cash_flow_quality(MockAnalysisService(dataset), request, "acc", ["acc"], None, user_id="u"))

    assert result.available is False
    assert result.is_healthy is False
