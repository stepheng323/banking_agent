from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from banking.transactions.query.handlers.insight import handle_insight
from banking.transactions.query.insights import insight_registry
from banking.transactions.query.insights.anomalies import execute_anomalies, resolve_anomalies_evidence
from banking.transactions.query.insights.counterparty_concentration import (
    execute_counterparty_concentration,
    resolve_counterparty_concentration_evidence,
)
from banking.transactions.query.insights.forecast import execute_forecast
from banking.transactions.query.insights.recurring_patterns import (
    execute_recurring_patterns,
    resolve_recurring_patterns_evidence,
)
from banking.transactions.query.models.domain import QueryRequest
from banking.transactions.query.models.operations import (
    AnalyzeOperation,
    AnomaliesSpec,
    CounterpartyConcentrationSpec,
    ForecastSpec,
    QueryScope,
    RecurringPatternsSpec,
    ResolvedPeriod,
)
from banking.transactions.query.services.analysis.kernel.contracts import (
    AnalysisDataset,
    CoverageStatus,
    ForecastResult,
)


class _AnalysisService:
    def __init__(self, dataset: AnalysisDataset) -> None:
        self.dataset = dataset

    async def load_dataset(self, *_args: Any, **_kwargs: Any) -> AnalysisDataset:
        return self.dataset


def _request(spec: Any, *, end: date = date(2026, 7, 27)) -> QueryRequest:
    return QueryRequest(
        operation=AnalyzeOperation(
            scope=QueryScope(period=ResolvedPeriod(start=end - timedelta(days=179), end=end)),
            analysis=spec,
        )
    )


def _row(
    identifier: str,
    *,
    when: date,
    amount: int,
    direction: str = "debit",
    counterparty: str = "Netflix",
    category: str = "subscriptions",
) -> dict[str, Any]:
    return {
        "transaction_id": identifier,
        "date": f"{when.isoformat()}T10:00:00+00:00",
        "amount": amount,
        "currency": "NGN",
        "status": "posted",
        "type": direction,
        "counterparty": counterparty,
        "counterparty_entity_id": counterparty.lower(),
        "source_account_id": "account-1",
        "event_type": "subscription" if category == "subscriptions" else "transfer",
        "category": category,
        "cash_flow_class": "operating",
        "semantic_resolution_state": "resolved",
    }


def _dataset(rows: list[dict[str, Any]], *, coverage: CoverageStatus = CoverageStatus.COMPLETE) -> AnalysisDataset:
    return AnalysisDataset(
        basis="economic_events",
        period_label="current",
        start_date=date(2026, 1, 29),
        end_date=date(2026, 7, 27),
        coverage_status=coverage,
        rows=rows,
    )


@pytest.mark.asyncio
async def test_recurring_surface_and_evidence_are_grounded() -> None:
    rows = [
        _row("r1", when=date(2026, 5, 1), amount=10_000),
        _row("r2", when=date(2026, 5, 31), amount=12_000),
        _row("r3", when=date(2026, 6, 30), amount=11_000),
    ]
    service = _AnalysisService(_dataset(rows))
    request = _request(RecurringPatternsSpec())
    result = await execute_recurring_patterns(service, request, "account-1", ["account-1"], None, user_id="user")
    presentation = insight_registry.get("recurring_patterns").presenter(result, "en")  # type: ignore[union-attr]

    assert len(presentation.items) == 1
    evidence = presentation.surface_view.items[0].payload.insight_evidence
    assert evidence is not None
    evidence_rows = await resolve_recurring_patterns_evidence(
        service,
        request,
        evidence,  # type: ignore[arg-type]
        "account-1",
        ["account-1"],
        None,
        user_id="user",
    )
    assert [item["id"] for item in evidence_rows] == ["r1", "r2", "r3"]


@pytest.mark.asyncio
async def test_irregular_intervals_do_not_become_monthly_recurrence() -> None:
    rows = [
        _row("r1", when=date(2026, 5, 1), amount=10_000),
        _row("r2", when=date(2026, 5, 2), amount=10_000),
        _row("r3", when=date(2026, 6, 30), amount=10_000),
    ]
    result = await execute_recurring_patterns(
        _AnalysisService(_dataset(rows)),
        _request(RecurringPatternsSpec()),
        "account-1",
        ["account-1"],
        None,
        user_id="user",
    )
    assert result.series == []


@pytest.mark.asyncio
async def test_credit_anomaly_has_income_metric_and_evidence() -> None:
    rows = [
        _row(f"salary-{index}", when=date(2026, 1, index + 1), amount=50_000, direction="credit", category="salary")
        for index in range(6)
    ]
    rows.append(_row("salary-high", when=date(2026, 1, 10), amount=250_000, direction="credit", category="salary"))
    service = _AnalysisService(_dataset(rows))
    request = _request(AnomaliesSpec())
    result = await execute_anomalies(service, request, "account-1", ["account-1"], None, user_id="user")
    presentation = insight_registry.get("anomalies").presenter(result, "en")  # type: ignore[union-attr]

    assert result.anomalies[0].metric == "income"
    evidence = presentation.surface_view.items[0].payload.insight_evidence
    evidence_rows = await resolve_anomalies_evidence(
        service,
        request,
        evidence,  # type: ignore[arg-type]
        "account-1",
        ["account-1"],
        None,
        user_id="user",
    )
    assert [item["id"] for item in evidence_rows] == ["salary-high"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("measure", "expected"),
    [
        ("spending", "Merchant"),
        ("outflow", "Merchant"),
        ("income", "Employer"),
        ("inflow", "Employer"),
    ],
)
async def test_concentration_implements_every_measure(measure: str, expected: str) -> None:
    rows = [
        _row("debit", when=date(2026, 7, 1), amount=80_000, counterparty="Merchant"),
        _row("credit", when=date(2026, 7, 2), amount=120_000, direction="credit", counterparty="Employer"),
    ]
    service = _AnalysisService(_dataset(rows))
    request = _request(CounterpartyConcentrationSpec(measure=measure))
    result = await execute_counterparty_concentration(
        service,
        request,
        "account-1",
        ["account-1"],
        None,
        user_id="user",
    )
    presentation = insight_registry.get("counterparty_concentration").presenter(  # type: ignore[union-attr]
        result,
        "en",
    )
    assert result.groups[0].counterparty == expected
    evidence = presentation.surface_view.items[0].payload.insight_evidence
    evidence_rows = await resolve_counterparty_concentration_evidence(
        service,
        request,
        evidence,  # type: ignore[arg-type]
        "account-1",
        ["account-1"],
        None,
        user_id="user",
    )
    assert len(evidence_rows) == 1


@pytest.mark.asyncio
async def test_partial_forecast_discloses_but_require_complete_blocks() -> None:
    rows = [_row("spend", when=date(2026, 7, 1), amount=30_000)]
    service = _AnalysisService(_dataset(rows, coverage=CoverageStatus.PARTIAL))
    disclosed = await execute_forecast(
        service,
        _request(ForecastSpec(history_days=180, completeness_policy="disclose")),
        "account-1",
        ["account-1"],
        None,
        user_id="user",
    )
    blocked = await execute_forecast(
        service,
        _request(ForecastSpec(history_days=180, completeness_policy="require_complete")),
        "account-1",
        ["account-1"],
        None,
        user_id="user",
    )
    assert disclosed.available is True
    assert disclosed.metadata is not None and disclosed.metadata.coverage == CoverageStatus.PARTIAL
    assert blocked.available is False


@pytest.mark.asyncio
async def test_handler_contains_presentation_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    definition = insight_registry.get("forecast")
    assert definition is not None

    async def _executor(**_kwargs: Any) -> ForecastResult:
        return ForecastResult(available=True)

    def _presenter(_result: Any, _language: str) -> Any:
        raise RuntimeError("presentation failed")

    monkeypatch.setattr(definition, "executor", _executor)
    monkeypatch.setattr(definition, "presenter", _presenter)
    result = await handle_insight(
        object(),  # type: ignore[arg-type]
        _request(ForecastSpec()),
        "account-1",
        ["account-1"],
    )
    assert result.summary_text
    assert result.items is None
