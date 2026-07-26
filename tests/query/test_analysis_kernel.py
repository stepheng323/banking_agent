from __future__ import annotations

from decimal import Decimal

import pytest

from banking.transactions.query.presentation.insights.variance import format_variance_result
from banking.transactions.query.services.analysis.kernel.contracts import (
    AnalysisDataset,
    AnalysisMetric,
    ComparisonSpec,
    CoverageStatus,
    Dimension,
    MetricSpec,
)
from banking.transactions.query.services.analysis.kernel.metrics import (
    _human_label,
    _row_key,
    analyze_cash_flow,
    analyze_variance,
    calculate_breakdowns,
    calculate_metric,
    compare_periods,
)


@pytest.fixture
def sample_rows() -> list[dict]:
    return [
        {
            "type": "debit",
            "amount": 20_000,
            "resolved_category": "transport",
            "counterparty": "Uber",
            "semantic_resolution_state": "resolved",
        },
        {
            "type": "debit",
            "amount": 5_000,
            "resolved_category": "food",
            "counterparty": "Mr Biggs",
            "semantic_resolution_state": "resolved",
        },
        {
            "type": "credit",
            "amount": 100_000,
            "resolved_category": "salary",
            "counterparty": "Employer",
            "semantic_resolution_state": "resolved",
        },
    ]


def test_calculate_metric_spending_excludes_internal(sample_rows: list[dict]) -> None:
    dataset = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=sample_rows,
    )
    spec = MetricSpec(metric=AnalysisMetric.SPENDING, basis="ledger_transactions")
    result = calculate_metric(dataset, spec)

    assert result.value == Decimal("25000")
    assert result.count == 2


def test_account_dimension_prefers_safe_account_label_over_internal_identifier() -> None:
    assert (
        _row_key(
            {
                "source_account_id": "6fe81b80-35df-4745-84c1-0a05f485b7e9",
                "source_account_label": "GTBank",
                "bank_name": "GTBank",
            },
            Dimension.ACCOUNT,
        )
        == "GTBank"
    )
    assert _human_label("GTBank", Dimension.ACCOUNT) == "GTBank"
    assert _human_label("6fe81b80-35df-4745-84c1-0a05f485b7e9", Dimension.ACCOUNT) == "linked account"


def test_calculate_metric_income(sample_rows: list[dict]) -> None:
    dataset = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=sample_rows,
    )
    spec = MetricSpec(metric=AnalysisMetric.INCOME, basis="ledger_transactions")
    result = calculate_metric(dataset, spec)

    assert result.value == Decimal("100000")
    assert result.count == 1


def test_calculate_metric_net_cash_flow(sample_rows: list[dict]) -> None:
    dataset = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=sample_rows,
    )
    spec = MetricSpec(metric=AnalysisMetric.NET_CASH_FLOW, basis="ledger_transactions")
    result = calculate_metric(dataset, spec)

    assert result.value == Decimal("75000")


def test_internal_transfer_excluded_from_spending() -> None:
    rows = [
        {"type": "debit", "amount": 10_000, "resolved_category": "transport", "is_internal_transfer": True},
        {"type": "debit", "amount": 5_000, "resolved_category": "food"},
    ]
    dataset = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=rows,
    )
    spec = MetricSpec(metric=AnalysisMetric.SPENDING, basis="ledger_transactions")
    result = calculate_metric(dataset, spec)

    assert result.value == Decimal("5000")
    assert result.excluded_value == Decimal("10000")


def test_non_operating_event_excluded_from_net() -> None:
    rows = [
        {"type": "debit", "amount": 10_000, "resolved_category": "savings", "cash_flow_class": "investing"},
        {"type": "debit", "amount": 5_000, "resolved_category": "food"},
    ]
    dataset = AnalysisDataset(
        basis="economic_events",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=rows,
    )
    spec = MetricSpec(metric=AnalysisMetric.NET_CASH_FLOW, basis="economic_events")
    result = calculate_metric(dataset, spec)

    assert result.value == Decimal("-5000")
    assert result.excluded_value == Decimal("10000")


def test_breakdown_by_category(sample_rows: list[dict]) -> None:
    dataset = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=sample_rows,
    )
    spec = MetricSpec(
        metric=AnalysisMetric.SPENDING,
        basis="ledger_transactions",
        dimensions=[Dimension.CATEGORY],
    )
    breakdowns = calculate_breakdowns(dataset, spec)

    assert len(breakdowns) == 1
    breakdown = breakdowns[0]
    assert breakdown.total == Decimal("25000")
    assert {bucket.key for bucket in breakdown.buckets} == {"transport", "food"}


def test_compare_periods_relative_status() -> None:
    current = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=[
            {"type": "debit", "amount": 10_000, "resolved_category": "food", "semantic_resolution_state": "resolved"}
        ],
    )
    baseline = AnalysisDataset(
        basis="ledger_transactions",
        period_label="baseline",
        start_date="2026-06-01",
        end_date="2026-06-30",
        rows=[{"type": "debit", "amount": 5_000, "resolved_category": "food", "semantic_resolution_state": "resolved"}],
    )
    current_metric = calculate_metric(current, MetricSpec(metric=AnalysisMetric.SPENDING, basis="ledger_transactions"))
    baseline_metric = calculate_metric(
        baseline, MetricSpec(metric=AnalysisMetric.SPENDING, basis="ledger_transactions")
    )
    comparison = compare_periods(current_metric, baseline_metric)

    assert comparison.absolute_delta == Decimal("5000")
    assert comparison.relative_delta == Decimal("1")
    assert comparison.relative_status == "increased"


def test_compare_periods_new_bucket() -> None:
    current = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=[{"type": "debit", "amount": 10_000, "resolved_category": "food"}],
    )
    baseline = AnalysisDataset(
        basis="ledger_transactions",
        period_label="baseline",
        start_date="2026-06-01",
        end_date="2026-06-30",
        rows=[],
    )
    current_metric = calculate_metric(current, MetricSpec(metric=AnalysisMetric.SPENDING, basis="ledger_transactions"))
    baseline_metric = calculate_metric(
        baseline, MetricSpec(metric=AnalysisMetric.SPENDING, basis="ledger_transactions")
    )
    comparison = compare_periods(current_metric, baseline_metric)

    assert comparison.relative_delta is None
    assert comparison.relative_status == "new"


def test_analyze_variance_residual_and_drivers() -> None:
    from banking.transactions.query.services.analysis.kernel.metrics import analyze_variance

    current = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=[
            {
                "type": "debit",
                "amount": 20_000,
                "resolved_category": "transport",
                "semantic_resolution_state": "resolved",
            },
            {"type": "debit", "amount": 5_000, "resolved_category": "food", "semantic_resolution_state": "resolved"},
        ],
    )
    baseline = AnalysisDataset(
        basis="ledger_transactions",
        period_label="baseline",
        start_date="2026-06-01",
        end_date="2026-06-30",
        rows=[
            {
                "type": "debit",
                "amount": 5_000,
                "resolved_category": "transport",
                "semantic_resolution_state": "resolved",
            },
            {"type": "debit", "amount": 5_000, "resolved_category": "food", "semantic_resolution_state": "resolved"},
        ],
    )
    spec = ComparisonSpec(
        metric=AnalysisMetric.SPENDING,
        basis="ledger_transactions",
        current=current,
        baseline=baseline,
        dimensions=[Dimension.CATEGORY],
        evidence_limit=5,
    )
    result = analyze_variance(current, baseline, spec)

    assert result.measure == "spending"
    assert result.metric_comparisons[0].absolute_delta == Decimal("15000")
    assert len(result.dimension_views) == 1
    drivers = result.dimension_views[0].drivers
    assert len(drivers) == 1
    assert drivers[0].key == "transport"
    assert drivers[0].absolute_delta == Decimal("15000")
    assert result.residual == Decimal("0")


def test_confidence_segment_uncertain_tracks_separately() -> None:
    rows = [
        {"type": "debit", "amount": 10_000, "resolved_category": "food", "semantic_resolution_state": "resolved"},
        {"type": "debit", "amount": 2_000, "resolved_category": "food", "semantic_resolution_state": "partial"},
    ]
    dataset = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=rows,
    )
    spec = MetricSpec(
        metric=AnalysisMetric.SPENDING,
        basis="ledger_transactions",
        confidence_policy="segment_uncertain",
    )
    result = calculate_metric(dataset, spec)

    assert result.value == Decimal("10000")
    assert result.uncertain_value == Decimal("2000")


def test_confidence_include_keeps_uncertain_in_total() -> None:
    rows = [
        {"type": "debit", "amount": 10_000, "resolved_category": "food", "semantic_resolution_state": "resolved"},
        {"type": "debit", "amount": 2_000, "resolved_category": "food", "semantic_resolution_state": "partial"},
    ]
    dataset = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=rows,
    )
    spec = MetricSpec(
        metric=AnalysisMetric.SPENDING,
        basis="ledger_transactions",
        confidence_policy="include",
    )
    result = calculate_metric(dataset, spec)

    assert result.value == Decimal("12000")
    assert result.uncertain_value == Decimal("2000")


def test_confidence_exclude_uncertain_omits_value() -> None:
    rows = [
        {"type": "debit", "amount": 10_000, "resolved_category": "food", "semantic_resolution_state": "resolved"},
        {"type": "debit", "amount": 2_000, "resolved_category": "food", "semantic_resolution_state": "partial"},
    ]
    dataset = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=rows,
    )
    spec = MetricSpec(
        metric=AnalysisMetric.SPENDING,
        basis="ledger_transactions",
        confidence_policy="exclude_uncertain",
    )
    result = calculate_metric(dataset, spec)

    assert result.value == Decimal("10000")
    assert result.uncertain_value == Decimal("0")


def test_economic_spending_excludes_investing() -> None:
    rows = [
        {
            "type": "debit",
            "amount": 10_000,
            "resolved_category": "savings",
            "cash_flow_class": "investing",
            "semantic_resolution_state": "resolved",
        },
        {
            "type": "debit",
            "amount": 5_000,
            "resolved_category": "food",
            "cash_flow_class": "operating",
            "semantic_resolution_state": "resolved",
        },
    ]
    dataset = AnalysisDataset(
        basis="economic_events",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=rows,
    )
    spec = MetricSpec(metric=AnalysisMetric.SPENDING, basis="economic_events")
    result = calculate_metric(dataset, spec)

    assert result.value == Decimal("5000")
    assert result.excluded_value == Decimal("10000")


def test_economic_income_excludes_financing() -> None:
    rows = [
        {
            "type": "credit",
            "amount": 50_000,
            "resolved_category": "loan",
            "cash_flow_class": "financing",
            "semantic_resolution_state": "resolved",
        },
        {
            "type": "credit",
            "amount": 100_000,
            "resolved_category": "salary",
            "cash_flow_class": "operating",
            "semantic_resolution_state": "resolved",
        },
    ]
    dataset = AnalysisDataset(
        basis="economic_events",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=rows,
    )
    spec = MetricSpec(metric=AnalysisMetric.INCOME, basis="economic_events")
    result = calculate_metric(dataset, spec)

    assert result.value == Decimal("100000")
    assert result.excluded_value == Decimal("50000")


def test_coverage_require_complete_returns_zero() -> None:
    rows = [
        {"type": "debit", "amount": 10_000, "resolved_category": "food", "semantic_resolution_state": "resolved"},
    ]
    dataset = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=rows,
        coverage_status=CoverageStatus.PARTIAL,
    )
    spec = MetricSpec(
        metric=AnalysisMetric.SPENDING,
        basis="ledger_transactions",
        completeness_policy="require_complete",
    )
    result = calculate_metric(dataset, spec)

    assert result.value == Decimal("0")
    assert result.coverage_status == CoverageStatus.PARTIAL


def test_dimension_deduplication_in_variance_spec() -> None:
    from banking.transactions.query.models.operations import VarianceDriversSpec

    directive = VarianceDriversSpec(
        dimensions=["category", "category", "counterparty", "account", "counterparty"],
    )
    assert directive.dimensions == ["category", "counterparty", "account"]


def test_economic_net_cash_flow_excludes_investing_and_financing() -> None:
    rows = [
        {
            "type": "debit",
            "amount": 10_000,
            "resolved_category": "savings",
            "cash_flow_class": "investing",
            "semantic_resolution_state": "resolved",
        },
        {
            "type": "credit",
            "amount": 50_000,
            "resolved_category": "loan",
            "cash_flow_class": "financing",
            "semantic_resolution_state": "resolved",
        },
        {
            "type": "credit",
            "amount": 100_000,
            "resolved_category": "salary",
            "cash_flow_class": "operating",
            "semantic_resolution_state": "resolved",
        },
        {
            "type": "debit",
            "amount": 5_000,
            "resolved_category": "food",
            "cash_flow_class": "operating",
            "semantic_resolution_state": "resolved",
        },
    ]
    dataset = AnalysisDataset(
        basis="economic_events",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=rows,
    )
    spec = MetricSpec(metric=AnalysisMetric.NET_CASH_FLOW, basis="economic_events")
    result = calculate_metric(dataset, spec)

    assert result.value == Decimal("95000")
    assert result.excluded_value == Decimal("60000")


def test_breakdown_segment_uncertain_excludes_from_total() -> None:
    rows = [
        {"type": "debit", "amount": 10_000, "resolved_category": "food", "semantic_resolution_state": "resolved"},
        {"type": "debit", "amount": 2_000, "resolved_category": "food", "semantic_resolution_state": "partial"},
    ]
    dataset = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=rows,
    )
    spec = MetricSpec(
        metric=AnalysisMetric.SPENDING,
        basis="ledger_transactions",
        dimensions=[Dimension.CATEGORY],
        confidence_policy="segment_uncertain",
    )
    breakdowns = calculate_breakdowns(dataset, spec)

    assert len(breakdowns) == 1
    assert breakdowns[0].total == Decimal("10000")
    assert sum(bucket.value for bucket in breakdowns[0].buckets) == Decimal("10000")
    assert breakdowns[0].uncertain_value == Decimal("2000")


def test_require_complete_blocks_breakdown() -> None:
    rows = [
        {"type": "debit", "amount": 10_000, "resolved_category": "food", "semantic_resolution_state": "resolved"},
    ]
    dataset = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=rows,
        coverage_status=CoverageStatus.PARTIAL,
    )
    spec = MetricSpec(
        metric=AnalysisMetric.SPENDING,
        basis="ledger_transactions",
        dimensions=[Dimension.CATEGORY],
        completeness_policy="require_complete",
    )
    breakdowns = calculate_breakdowns(dataset, spec)

    assert len(breakdowns) == 0


def test_require_complete_renders_unavailable_instead_of_zero_activity() -> None:
    current = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=[{"type": "debit", "amount": 10_000, "resolved_category": "food"}],
        coverage_status=CoverageStatus.PARTIAL,
    )
    baseline = AnalysisDataset(
        basis="ledger_transactions",
        period_label="baseline",
        start_date="2026-06-01",
        end_date="2026-06-30",
        rows=[],
        coverage_status=CoverageStatus.COMPLETE,
    )
    result = analyze_variance(
        current,
        baseline,
        ComparisonSpec(
            metric=AnalysisMetric.SPENDING,
            basis="ledger_transactions",
            current=current,
            baseline=baseline,
            dimensions=[Dimension.CATEGORY],
            completeness_policy="require_complete",
        ),
    )

    assert result.available is False
    assert format_variance_result(result, language="en").startswith("I can't calculate")


def test_excluded_value_only_counts_policy_exclusions() -> None:
    rows = [
        {"type": "debit", "amount": 10_000, "resolved_category": "food", "is_internal_transfer": True},
        {"type": "credit", "amount": 5_000, "resolved_category": "salary"},
        {"type": "debit", "amount": 2_000, "resolved_category": "transport"},
    ]
    dataset = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=rows,
    )
    spec = MetricSpec(metric=AnalysisMetric.SPENDING, basis="ledger_transactions")
    result = calculate_metric(dataset, spec)

    assert result.value == Decimal("2000")
    assert result.excluded_value == Decimal("10000")


def test_format_variance_result_no_change() -> None:
    current = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=[
            {"type": "debit", "amount": 10_000, "resolved_category": "food", "semantic_resolution_state": "resolved"}
        ],
    )
    baseline = AnalysisDataset(
        basis="ledger_transactions",
        period_label="baseline",
        start_date="2026-06-01",
        end_date="2026-06-30",
        rows=[
            {"type": "debit", "amount": 10_000, "resolved_category": "food", "semantic_resolution_state": "resolved"}
        ],
    )
    spec = ComparisonSpec(
        metric=AnalysisMetric.SPENDING,
        basis="ledger_transactions",
        current=current,
        baseline=baseline,
        dimensions=[Dimension.CATEGORY],
        evidence_limit=5,
    )
    result = analyze_variance(current, baseline, spec)
    text = format_variance_result(result, language="en")

    assert "unchanged" in text or "same" in text.lower()
    assert "Food" not in text or "drivers" not in text.lower()


def test_metric_exclusion_flags_are_authoritative() -> None:
    dataset = AnalysisDataset(
        basis="economic_events",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=[
            {
                "type": "debit",
                "amount": 10_000,
                "cash_flow_class": "investing",
                "is_internal_transfer": True,
            }
        ],
    )
    result = calculate_metric(
        dataset,
        MetricSpec(
            metric=AnalysisMetric.SPENDING,
            basis="economic_events",
            exclude_internal=False,
            exclude_financing_investing_for_operating=False,
        ),
    )

    assert result.value == Decimal("10000")
    assert result.excluded_value == Decimal("0")


def test_unavailable_period_is_not_downgraded_to_partial() -> None:
    current = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        coverage_status=CoverageStatus.UNAVAILABLE,
    )
    baseline = AnalysisDataset(
        basis="ledger_transactions",
        period_label="baseline",
        start_date="2026-06-01",
        end_date="2026-06-30",
    )
    result = analyze_variance(
        current,
        baseline,
        ComparisonSpec(
            metric=AnalysisMetric.SPENDING,
            basis="ledger_transactions",
            current=current,
            baseline=baseline,
            dimensions=[Dimension.CATEGORY],
        ),
    )

    assert result.coverage == CoverageStatus.UNAVAILABLE


def test_cash_flow_analysis_excludes_unsettled_rows() -> None:
    dataset = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=[
            {"type": "debit", "amount": 5_000, "status": "successful"},
            {"type": "debit", "amount": 50_000, "status": "failed"},
        ],
    )

    result = analyze_cash_flow(dataset)

    assert result.outflow.value == Decimal("5000")
    assert result.excluded_unsettled_count == 1


@pytest.mark.asyncio
async def test_cash_flow_overview_deduplicates_disclosures_and_builds_each_metric_view() -> None:
    from banking.transactions.query.insights.variance import _execute_cash_flow_overview

    current = AnalysisDataset(
        basis="economic_events",
        period_label="current",
        start_date="2026-07-01",
        end_date="2026-07-31",
        rows=[
            {
                "type": "debit",
                "amount": 100,
                "resolved_category": "savings",
                "cash_flow_class": "investing",
                "semantic_resolution_state": "resolved",
            },
            {
                "type": "credit",
                "amount": 200,
                "resolved_category": "loan",
                "cash_flow_class": "financing",
                "semantic_resolution_state": "resolved",
            },
            {
                "type": "debit",
                "amount": 30,
                "resolved_category": "food",
                "cash_flow_class": "operating",
                "semantic_resolution_state": "partial",
            },
        ],
    )
    baseline = AnalysisDataset(
        basis="economic_events",
        period_label="baseline",
        start_date="2026-06-01",
        end_date="2026-06-30",
    )

    result = await _execute_cash_flow_overview(
        current,
        baseline,
        "economic_events",
        [Dimension.CATEGORY],
        "segment_uncertain",
        "disclose",
        5,
    )

    assert result.excluded_value == Decimal("300")
    assert result.uncertainty == Decimal("30")
    assert {view.metric for view in result.dimension_views} == {
        AnalysisMetric.INCOME,
        AnalysisMetric.SPENDING,
        AnalysisMetric.NET_CASH_FLOW,
    }


def _variance_acceptance_dataset(*, period_label: str, rows: list[dict]) -> AnalysisDataset:
    return AnalysisDataset(
        basis="economic_events",
        period_label=period_label,
        start_date="2026-06-01" if period_label == "baseline" else "2026-07-01",
        end_date="2026-06-30" if period_label == "baseline" else "2026-07-31",
        rows=rows,
        coverage_status=CoverageStatus.COMPLETE,
    )


@pytest.mark.asyncio
async def test_variance_acceptance_fixture_has_expected_operating_totals_drivers_and_evidence() -> None:
    from banking.transactions.query.insights.variance import execute_variance_drivers

    current = _variance_acceptance_dataset(
        period_label="current",
        rows=[
            {
                "transaction_id": "current-food",
                "type": "debit",
                "amount": 60_000,
                "resolved_category": "food",
                "counterparty": "Food Village",
                "cash_flow_class": "operating",
                "semantic_resolution_state": "resolved",
                "status": "posted",
            },
            {
                "transaction_id": "current-transport",
                "type": "debit",
                "amount": 30_000,
                "resolved_category": "transport",
                "counterparty": "Uber",
                "cash_flow_class": "operating",
                "semantic_resolution_state": "resolved",
                "status": "posted",
            },
            {
                "transaction_id": "current-health",
                "type": "debit",
                "amount": 7_000,
                "resolved_category": "health",
                "counterparty": "Medical Clinic",
                "cash_flow_class": "operating",
                "semantic_resolution_state": "resolved",
                "status": "posted",
            },
            {
                "transaction_id": "current-piggyvest",
                "type": "debit",
                "amount": 50_000,
                "resolved_category": "savings",
                "cash_flow_class": "investing",
                "semantic_resolution_state": "resolved",
                "status": "posted",
            },
            {
                "transaction_id": "current-own",
                "type": "debit",
                "amount": 10_000,
                "cash_flow_class": "internal",
                "semantic_resolution_state": "resolved",
                "status": "posted",
            },
            {
                "transaction_id": "current-salary",
                "type": "credit",
                "amount": 700_000,
                "resolved_category": "salary",
                "cash_flow_class": "operating",
                "semantic_resolution_state": "resolved",
                "status": "posted",
            },
            {
                "transaction_id": "current-loan",
                "type": "credit",
                "amount": 100_000,
                "cash_flow_class": "financing",
                "semantic_resolution_state": "resolved",
                "status": "posted",
            },
            {
                "transaction_id": "current-unknown",
                "type": "debit",
                "amount": 9_000,
                "cash_flow_class": "operating",
                "semantic_resolution_state": "partial",
                "status": "posted",
            },
            {
                "transaction_id": "current-pending",
                "type": "debit",
                "amount": 4_000,
                "resolved_category": "food",
                "cash_flow_class": "operating",
                "semantic_resolution_state": "resolved",
                "status": "pending",
            },
            {
                "transaction_id": "current-reversed",
                "type": "debit",
                "amount": 3_000,
                "resolved_category": "food",
                "cash_flow_class": "operating",
                "semantic_resolution_state": "resolved",
                "status": "reversed",
            },
        ],
    )
    baseline = _variance_acceptance_dataset(
        period_label="baseline",
        rows=[
            {
                "transaction_id": "baseline-food",
                "type": "debit",
                "amount": 20_000,
                "resolved_category": "food",
                "counterparty": "Food Village",
                "cash_flow_class": "operating",
                "semantic_resolution_state": "resolved",
                "status": "posted",
            },
            {
                "transaction_id": "baseline-transport",
                "type": "debit",
                "amount": 8_000,
                "resolved_category": "transport",
                "counterparty": "Uber",
                "cash_flow_class": "operating",
                "semantic_resolution_state": "resolved",
                "status": "posted",
            },
            {
                "transaction_id": "baseline-health",
                "type": "debit",
                "amount": 7_000,
                "resolved_category": "health",
                "counterparty": "Medical Clinic",
                "cash_flow_class": "operating",
                "semantic_resolution_state": "resolved",
                "status": "posted",
            },
            {
                "transaction_id": "baseline-piggyvest",
                "type": "debit",
                "amount": 50_000,
                "resolved_category": "savings",
                "cash_flow_class": "investing",
                "semantic_resolution_state": "resolved",
                "status": "posted",
            },
            {
                "transaction_id": "baseline-own",
                "type": "debit",
                "amount": 10_000,
                "cash_flow_class": "internal",
                "semantic_resolution_state": "resolved",
                "status": "posted",
            },
            {
                "transaction_id": "baseline-salary",
                "type": "credit",
                "amount": 600_000,
                "resolved_category": "salary",
                "cash_flow_class": "operating",
                "semantic_resolution_state": "resolved",
                "status": "posted",
            },
            {
                "transaction_id": "baseline-loan",
                "type": "credit",
                "amount": 100_000,
                "cash_flow_class": "financing",
                "semantic_resolution_state": "resolved",
                "status": "posted",
            },
            {
                "transaction_id": "baseline-unknown",
                "type": "debit",
                "amount": 9_000,
                "cash_flow_class": "operating",
                "semantic_resolution_state": "partial",
                "status": "posted",
            },
        ],
    )
    result = await execute_variance_drivers(
        current_dataset=current,
        baseline_dataset=baseline,
        measure="spending",
        dimensions=["category", "counterparty"],
        basis="economic_events",
        confidence_policy="segment_uncertain",
        completeness_policy="disclose",
        evidence_limit=5,
    )
    spending = result.metric_comparisons[0]

    assert spending.current_value == Decimal("97000")
    assert spending.baseline_value == Decimal("35000")
    assert spending.absolute_delta == Decimal("62000")
    assert result.excluded_value == Decimal("127000")
    assert result.uncertainty == Decimal("18000")
    assert result.residual == Decimal("0")
    category_drivers = next(view.drivers for view in result.dimension_views if view.dimension == Dimension.CATEGORY)
    assert [(driver.key, driver.absolute_delta) for driver in category_drivers] == [
        ("food", Decimal("40000")),
        ("transport", Decimal("22000")),
    ]
    food_selector = next(selector for selector in result.evidence_selectors if selector.bucket_key == "food")
    rows = [
        row
        for row in current.rows
        if row["transaction_id"] == "current-food" and row["resolved_category"] == food_selector.bucket_key
    ]
    assert [row["transaction_id"] for row in rows] == ["current-food"]

    income = calculate_metric(current, MetricSpec(metric=AnalysisMetric.INCOME, basis="economic_events"))
    assert income.value == Decimal("700000")
    assert income.excluded_value == Decimal("100000")


def test_variance_acceptance_fixture_keeps_coverage_and_no_change_copy_honest() -> None:
    rows = [{"type": "debit", "amount": 7_000, "resolved_category": "health", "semantic_resolution_state": "resolved"}]
    current = _variance_acceptance_dataset(period_label="current", rows=rows)
    baseline = _variance_acceptance_dataset(period_label="baseline", rows=rows)
    result = analyze_variance(
        current,
        baseline,
        ComparisonSpec(
            metric=AnalysisMetric.SPENDING,
            basis="economic_events",
            current=current,
            baseline=baseline,
            dimensions=[Dimension.CATEGORY],
        ),
    )
    assert "same" in format_variance_result(result, language="en").lower()

    unavailable = current.model_copy(update={"coverage_status": CoverageStatus.UNAVAILABLE})
    blocked = analyze_variance(
        unavailable,
        baseline,
        ComparisonSpec(
            metric=AnalysisMetric.SPENDING,
            basis="economic_events",
            current=unavailable,
            baseline=baseline,
            dimensions=[Dimension.CATEGORY],
            completeness_policy="require_complete",
        ),
    )
    assert blocked.available is False
    assert "can't calculate" in format_variance_result(blocked, language="en").lower()
