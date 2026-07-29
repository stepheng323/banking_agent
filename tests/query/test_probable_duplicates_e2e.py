from datetime import date, timedelta

import pytest

from banking.transactions.query.insights.probable_duplicates import (
    build_probable_duplicates_surface,
    execute_probable_duplicates,
    resolve_probable_duplicates_evidence,
)
from banking.transactions.query.models.operations import (
    AnalyzeOperation,
    ProbableDuplicatesSpec,
    QueryRequest,
    QueryScope,
    ResolvedPeriod,
)
from banking.transactions.query.services.analysis.kernel.contracts import AnalysisDataset, CoverageStatus


class MockAnalysisService:
    def __init__(self):
        self.requested_periods = []

    async def load_dataset(
        self,
        contract: QueryRequest,
        account_id: str,
        account_ids: list[str],
        accounts_info: list[dict] | None,
        *,
        user_id: str | None,
        basis: str,
        period_label: str,
    ) -> AnalysisDataset:
        period = contract.operation.scope.period
        self.requested_periods.append(period)

        # Return dummy transactions inside the period
        return AnalysisDataset(
            basis="ledger_transactions",
            period_label="current",
            start_date=period.start,
            end_date=period.end,
            coverage_status=CoverageStatus.COMPLETE,
            rows=[
                {
                    "transaction_id": "1",
                    "date": f"{period.end.isoformat()}T10:00:00Z",
                    "amount": 5000.0,
                    "status": "posted",
                    "type": "debit",
                    "counterparty": "Merchant A",
                    "reference": "REF1",
                    "event_type": "card_payment",
                    "cash_flow_class": "operating",
                    "is_internal_transfer": False,
                },
                {
                    "transaction_id": "2",
                    "date": f"{period.end.isoformat()}T10:00:00Z",
                    "amount": 5000.0,
                    "status": "posted",
                    "type": "debit",
                    "counterparty": "Merchant A",
                    "reference": "REF1",
                    "event_type": "card_payment",
                    "cash_flow_class": "operating",
                    "is_internal_transfer": False,
                },
            ],
        )


@pytest.mark.asyncio
async def test_probable_duplicates_e2e_window_drill_down() -> None:
    service = MockAnalysisService()

    # Original request for "today"
    today = date(2023, 10, 31)

    contract = QueryRequest(
        operation=AnalyzeOperation(
            scope=QueryScope(
                period=ResolvedPeriod(start=today, end=today),
            ),
            analysis=ProbableDuplicatesSpec(lookback_days=90),
        )
    )

    # 1. Execute
    result = await execute_probable_duplicates(service, contract, "acc_1", ["acc_1"], None, user_id="user_1")

    # Assert execution used expanded window
    assert len(service.requested_periods) == 1
    exec_period = service.requested_periods[0]
    assert exec_period.end == today
    assert exec_period.start == today - timedelta(days=89)

    # Assert result surface correctly carries the evidence with effective window
    surface = build_probable_duplicates_surface(result)
    item = surface.items[0]
    evidence = item.payload.insight_evidence
    assert evidence.insight_type == "probable_duplicates"
    assert evidence.effective_start == exec_period.start.isoformat()
    assert evidence.effective_end == exec_period.end.isoformat()

    # 2. Drill-down
    drill_items = await resolve_probable_duplicates_evidence(
        service, contract, evidence, "acc_1", ["acc_1"], None, user_id="user_1"
    )

    # Assert drill down fetches exactly the same window
    assert len(service.requested_periods) == 2
    drill_period = service.requested_periods[1]
    assert drill_period.start == exec_period.start
    assert drill_period.end == exec_period.end
    assert len(drill_items) == 2
