"""Financial metric, breakdown, comparison, and variance calculations."""

from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any

from banking.transactions.query.services.analysis.kernel.contracts import (
    AnalysisDataset,
    AnalysisMetric,
    Bucket,
    CashFlowAnalysis,
    ComparisonSpec,
    ConfidencePolicy,
    CoverageStatus,
    Dimension,
    DimensionBreakdown,
    MetricResult,
    MetricSpec,
    PeriodComparison,
    VarianceAnalysisResult,
    VarianceDimensionView,
    VarianceDriver,
)

_UNRESOLVED_KEY = "__unresolved__"


# Economic-event classes that are not part of ordinary operating cash flow.
_NON_OPERATING_CASH_FLOW_CLASSES = {"financing", "investing"}


class _RowDisposition(str, Enum):
    """How a row relates to a metric."""

    INCLUDED = "included"
    UNCERTAIN_INCLUDED = "uncertain_included"  # include policy: in total, also disclosed
    UNCERTAIN_SEGMENTED = "uncertain_segmented"  # segment policy: separate from total
    EXCLUDED_INTERNAL = "excluded_internal"
    EXCLUDED_NON_OPERATING = "excluded_non_operating"
    EXCLUDED_UNCERTAIN = "excluded_uncertain"
    EXCLUDED_UNSETTLED = "excluded_unsettled"
    OUT_OF_SCOPE = "out_of_scope"


def _money(value: Any) -> Decimal:
    """Normalize an amount to a non-negative Decimal."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value.copy_abs()
    return Decimal(str(value)).copy_abs()


def _row_amount(row: dict[str, Any]) -> Decimal:
    return _money(row.get("amount", 0))


def _row_type(row: dict[str, Any]) -> str:
    return str(row.get("type") or row.get("transaction_type") or "").lower()


def _is_internal(row: dict[str, Any]) -> bool:
    if row.get("is_internal_transfer"):
        return True
    if row.get("cash_flow_class") == "internal":
        return True
    narration = str(row.get("narration", "")).lower()
    return "internal transfer" in narration or "own account" in narration


def _is_non_operating_event(row: dict[str, Any]) -> bool:
    """Return True for financing/investing economic events."""
    cash_flow_class = str(row.get("cash_flow_class") or "").lower()
    return cash_flow_class in _NON_OPERATING_CASH_FLOW_CLASSES


def _is_uncertain(row: dict[str, Any]) -> bool:
    return row.get("semantic_resolution_state") in {"partial", "needs_review", "unknown"}


def _is_settled(row: dict[str, Any]) -> bool:
    raw_status = (
        row.get("display_status") or row.get("status") or row.get("local_status") or row.get("provider_status") or ""
    )
    status = " ".join(str(raw_status).strip().lower().replace("_", " ").split())
    return status in {"", "posted", "success", "successful", "completed", "complete", "confirmed"}


def _normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def _coalesce_key(*values: Any, normalize: bool = True) -> str:
    for value in values:
        if value:
            text = str(value).strip()
            if normalize:
                text = text.lower()
            return text
    return _UNRESOLVED_KEY


def _row_key(row: dict[str, Any], dimension: Dimension) -> str:
    """Extract a stable bucket key for a row along a dimension."""
    if dimension == Dimension.CATEGORY:
        return _coalesce_key(row.get("resolved_category"), row.get("category"))
    if dimension == Dimension.COUNTERPARTY:
        return _coalesce_key(row.get("counterparty"), row.get("counterparty_entity_id"))
    if dimension == Dimension.ACCOUNT:
        # Account IDs are stable join keys, not safe presentation labels. The
        # projection carries a linked-account/bank label for this dimension;
        # only use an ID as a final internal fallback.
        return _coalesce_key(
            row.get("source_account_label"),
            row.get("bank_name"),
            row.get("source_account_id"),
            row.get("account_id"),
            normalize=False,
        )
    if dimension == Dimension.EVENT_TYPE:
        return _coalesce_key(row.get("event_type"), row.get("transaction_type"))
    if dimension == Dimension.CASH_FLOW_CLASS:
        return _coalesce_key(row.get("cash_flow_class"))
    return _UNRESOLVED_KEY


def _human_label(key: str, dimension: Dimension, language: str = "en") -> str:
    from banking.presentation.i18n.renderer import render_message

    if key == _UNRESOLVED_KEY or (dimension == Dimension.COUNTERPARTY and not key):
        return render_message("query.insight.unclassified", language)
    if dimension == Dimension.ACCOUNT:
        compact = key.replace("-", "")
        if len(compact) == 32 and all(char in "0123456789abcdefABCDEF" for char in compact):
            return render_message("context_frame.noun.linked_account", language)
        # Bank/account labels are already presentation-safe. Preserve their
        # provider casing (for example, GTBank) instead of title-casing them.
        return key
    return key.replace("_", " ").title()


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _in_scope_direction(row: dict[str, Any], metric: AnalysisMetric) -> bool:
    """Return whether the row's direction matches the metric."""
    tx_type = _row_type(row)
    if metric in {AnalysisMetric.SPENDING, AnalysisMetric.OUTFLOW}:
        return tx_type == "debit"
    if metric in {AnalysisMetric.INCOME, AnalysisMetric.INFLOW}:
        return tx_type == "credit"
    if metric == AnalysisMetric.NET_CASH_FLOW:
        return tx_type in {"credit", "debit"}
    return False


def _classify_row(row: dict[str, Any], spec: MetricSpec) -> _RowDisposition:
    """Classify how a row should be treated for a metric."""
    if not _in_scope_direction(row, spec.metric):
        return _RowDisposition.OUT_OF_SCOPE

    if not _is_settled(row):
        return _RowDisposition.EXCLUDED_UNSETTLED

    if spec.exclude_internal and _is_internal(row):
        return _RowDisposition.EXCLUDED_INTERNAL

    # Raw inflow/outflow retain financing and investing movements. Operating
    # spending/income/net may exclude them according to the explicit policy.
    if (
        spec.basis == "economic_events"
        and spec.exclude_financing_investing_for_operating
        and spec.metric in {AnalysisMetric.SPENDING, AnalysisMetric.INCOME, AnalysisMetric.NET_CASH_FLOW}
        and _is_non_operating_event(row)
    ):
        return _RowDisposition.EXCLUDED_NON_OPERATING

    if _is_uncertain(row):
        if spec.confidence_policy == ConfidencePolicy.EXCLUDE_UNCERTAIN:
            return _RowDisposition.EXCLUDED_UNCERTAIN
        if spec.confidence_policy == ConfidencePolicy.SEGMENT_UNCERTAIN:
            return _RowDisposition.UNCERTAIN_SEGMENTED
        return _RowDisposition.UNCERTAIN_INCLUDED

    return _RowDisposition.INCLUDED


def _signed_amount(row: dict[str, Any], metric: AnalysisMetric) -> Decimal:
    amount = _row_amount(row)
    if metric == AnalysisMetric.NET_CASH_FLOW:
        return amount if _row_type(row) == "credit" else -amount
    return amount


def _should_block_due_to_completeness(dataset: AnalysisDataset, spec: MetricSpec) -> bool:
    """Return True when the completeness policy forbids producing results."""
    if spec.completeness_policy == "require_complete" and dataset.coverage_status != CoverageStatus.COMPLETE:
        return True
    return False


def calculate_metric(
    dataset: AnalysisDataset,
    spec: MetricSpec,
) -> MetricResult:
    """Calculate a single metric from a dataset."""
    included_value = Decimal("0")
    excluded_value = Decimal("0")
    uncertain_value = Decimal("0")
    included_count = 0
    excluded_count = 0
    uncertain_count = 0

    for row in dataset.rows:
        disposition = _classify_row(row, spec)
        amount = _row_amount(row)

        if disposition == _RowDisposition.OUT_OF_SCOPE:
            continue

        if disposition in {
            _RowDisposition.EXCLUDED_INTERNAL,
            _RowDisposition.EXCLUDED_NON_OPERATING,
            _RowDisposition.EXCLUDED_UNCERTAIN,
            _RowDisposition.EXCLUDED_UNSETTLED,
        }:
            excluded_value += amount
            excluded_count += 1
            continue

        signed = _signed_amount(row, spec.metric)

        if disposition == _RowDisposition.UNCERTAIN_SEGMENTED:
            uncertain_value += signed.copy_abs()
            uncertain_count += 1
            continue

        if disposition == _RowDisposition.UNCERTAIN_INCLUDED:
            uncertain_value += signed.copy_abs()
            uncertain_count += 1

        included_value += signed
        included_count += 1

    blocked = _should_block_due_to_completeness(dataset, spec)
    value = Decimal("0") if blocked else included_value

    return MetricResult(
        metric=spec.metric,
        basis=spec.basis,
        value=_round_money(value),
        count=included_count,
        included_value=_round_money(included_value),
        excluded_value=_round_money(excluded_value),
        excluded_count=excluded_count,
        uncertain_value=_round_money(uncertain_value),
        uncertain_count=uncertain_count,
        coverage_status=dataset.coverage_status,
        completeness_policy=spec.completeness_policy,
        available=not blocked,
        unavailable_reason="coverage_incomplete" if blocked else None,
    )


def _aggregate_dimension(
    dataset: AnalysisDataset,
    spec: MetricSpec,
    dimension: Dimension,
    *,
    language: str = "en",
) -> DimensionBreakdown:
    """Break a metric down by a single dimension."""
    if _should_block_due_to_completeness(dataset, spec):
        return DimensionBreakdown(
            dimension=dimension,
            metric=spec.metric,
            basis=spec.basis,
            total=Decimal("0"),
            total_count=0,
            buckets=[],
        )

    bucket_values: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    bucket_counts: dict[str, int] = defaultdict(int)
    unresolved_value = Decimal("0")
    unresolved_count = 0
    excluded_value = Decimal("0")
    excluded_count = 0
    uncertain_value = Decimal("0")
    uncertain_count = 0
    total = Decimal("0")
    total_count = 0

    for row in dataset.rows:
        disposition = _classify_row(row, spec)
        amount = _row_amount(row)

        if disposition == _RowDisposition.OUT_OF_SCOPE:
            continue

        if disposition in {
            _RowDisposition.EXCLUDED_INTERNAL,
            _RowDisposition.EXCLUDED_NON_OPERATING,
            _RowDisposition.EXCLUDED_UNCERTAIN,
            _RowDisposition.EXCLUDED_UNSETTLED,
        }:
            excluded_value += amount
            excluded_count += 1
            continue

        signed = _signed_amount(row, spec.metric)
        is_uncertain = disposition in {_RowDisposition.UNCERTAIN_SEGMENTED, _RowDisposition.UNCERTAIN_INCLUDED}

        if disposition == _RowDisposition.UNCERTAIN_SEGMENTED:
            uncertain_value += signed.copy_abs()
            uncertain_count += 1
            continue

        key = _row_key(row, dimension)

        if key == _UNRESOLVED_KEY:
            unresolved_value += signed
            unresolved_count += 1
        else:
            bucket_values[key] += signed
            bucket_counts[key] += 1

        if is_uncertain:
            uncertain_value += signed.copy_abs()
            uncertain_count += 1
        total += signed
        total_count += 1

    buckets = [
        Bucket(
            key=key,
            label=_human_label(key, dimension, language=language),
            value=_round_money(value),
            count=bucket_counts[key],
        )
        for key, value in sorted(bucket_values.items(), key=lambda item: item[1].copy_abs(), reverse=True)
        if value != 0
    ]

    unresolved_bucket = None
    if unresolved_value != 0 or unresolved_count:
        unresolved_bucket = Bucket(
            key=_UNRESOLVED_KEY,
            label=_human_label(_UNRESOLVED_KEY, dimension, language=language),
            value=_round_money(unresolved_value),
            count=unresolved_count,
        )

    return DimensionBreakdown(
        dimension=dimension,
        metric=spec.metric,
        basis=spec.basis,
        total=_round_money(total),
        total_count=total_count,
        buckets=buckets,
        unresolved_bucket=unresolved_bucket,
        excluded_value=_round_money(excluded_value),
        excluded_count=excluded_count,
        uncertain_value=_round_money(uncertain_value),
        uncertain_count=uncertain_count,
    )


def calculate_breakdowns(
    dataset: AnalysisDataset,
    spec: MetricSpec,
    *,
    language: str = "en",
) -> list[DimensionBreakdown]:
    """Break a metric down by all requested dimensions."""
    if _should_block_due_to_completeness(dataset, spec):
        return []
    return [_aggregate_dimension(dataset, spec, dimension, language=language) for dimension in spec.dimensions]


def select_metric_rows(dataset: AnalysisDataset, spec: MetricSpec) -> list[dict[str, Any]]:
    """Return rows contributing to the authoritative metric value."""
    if _should_block_due_to_completeness(dataset, spec):
        return []
    included = {_RowDisposition.INCLUDED, _RowDisposition.UNCERTAIN_INCLUDED}
    return [row for row in dataset.rows if _classify_row(row, spec) in included]


def filter_dimension_rows(
    dataset: AnalysisDataset,
    spec: MetricSpec,
    *,
    dimension: Dimension,
    bucket_key: str,
) -> list[dict[str, Any]]:
    """Resolve a typed variance driver to its contributing current-period rows."""
    return [row for row in select_metric_rows(dataset, spec) if _row_key(row, dimension) == bucket_key]


def analyze_cash_flow(
    dataset: AnalysisDataset,
    *,
    confidence_policy: ConfidencePolicy = ConfidencePolicy.SEGMENT_UNCERTAIN,
    completeness_policy: str = "disclose",
) -> CashFlowAnalysis:
    """Calculate canonical inflow, outflow, net, and contributing rows once."""
    common = {
        "basis": dataset.basis,
        "confidence_policy": confidence_policy,
        "completeness_policy": completeness_policy,
    }
    inflow_spec = MetricSpec(metric=AnalysisMetric.INFLOW, **common)  # type: ignore[arg-type]
    outflow_spec = MetricSpec(metric=AnalysisMetric.OUTFLOW, **common)  # type: ignore[arg-type]
    net_spec = MetricSpec(
        metric=AnalysisMetric.NET_CASH_FLOW,
        exclude_financing_investing_for_operating=False,
        **common,  # type: ignore[arg-type]
    )
    inflow = calculate_metric(dataset, inflow_spec)
    outflow = calculate_metric(dataset, outflow_spec)
    net = calculate_metric(dataset, net_spec)
    settled_rows = select_metric_rows(dataset, net_spec)
    dispositions = [_classify_row(row, net_spec) for row in dataset.rows]
    return CashFlowAnalysis(
        basis=dataset.basis,
        inflow=inflow,
        outflow=outflow,
        net_cash_flow=net,
        settled_rows=settled_rows,
        excluded_internal_count=sum(disposition == _RowDisposition.EXCLUDED_INTERNAL for disposition in dispositions),
        excluded_unsettled_count=sum(disposition == _RowDisposition.EXCLUDED_UNSETTLED for disposition in dispositions),
    )


def _relative_status(current: Decimal, baseline: Decimal) -> Any:
    """Return a human-readable relative status for a metric change."""
    if current == baseline:
        return "unchanged"
    if baseline == 0 and current > 0:
        return "new"
    if current == 0 and baseline > 0:
        return "ended"
    return "increased" if current > baseline else "decreased"


def _calculate_relative_delta(current: Decimal, baseline: Decimal) -> Decimal | None:
    """Return relative change as a decimal ratio, or None for new/ended."""
    if baseline == 0 or current == 0:
        return None
    return _round_money((current - baseline) / baseline)


def compare_periods(
    current: MetricResult,
    baseline: MetricResult,
) -> PeriodComparison:
    """Compare a metric across two periods."""
    absolute_delta = current.value - baseline.value
    relative_delta = _calculate_relative_delta(current.value, baseline.value)
    return PeriodComparison(
        metric=current.metric,
        basis=current.basis,
        current_value=current.value,
        current_count=current.count,
        baseline_value=baseline.value,
        baseline_count=baseline.count,
        absolute_delta=absolute_delta,
        relative_delta=relative_delta,
        relative_status=_relative_status(current.value, baseline.value),
    )


def _build_variance_drivers(
    current: DimensionBreakdown,
    baseline: DimensionBreakdown,
    *,
    evidence_limit: int,
    language: str = "en",
) -> tuple[list[VarianceDriver], list[VarianceDriver]]:
    """Return (primary drivers, outlier drivers) for a dimension comparison.

    Primary drivers are ranked by absolute contribution up to evidence_limit.
    Outliers are relative movers not already in the primary set.
    """
    keys = {bucket.key for bucket in current.buckets} | {bucket.key for bucket in baseline.buckets}
    current_by_key = {bucket.key: bucket for bucket in current.buckets}
    baseline_by_key = {bucket.key: bucket for bucket in baseline.buckets}

    candidates: list[VarianceDriver] = []
    for key in keys:
        cur = current_by_key.get(key)
        base = baseline_by_key.get(key)
        current_value = cur.value if cur else Decimal("0")
        baseline_value = base.value if base else Decimal("0")
        absolute_delta = current_value - baseline_value
        if absolute_delta == 0:
            continue
        relative_delta = _calculate_relative_delta(current_value, baseline_value)
        candidates.append(
            VarianceDriver(
                dimension=current.dimension,
                key=key,
                label=_human_label(key, current.dimension, language=language),
                current_value=current_value,
                baseline_value=baseline_value,
                absolute_delta=absolute_delta,
                relative_delta=relative_delta,
                relative_status=_relative_status(current_value, baseline_value),
            )
        )

    # Deterministic tie-breaker: absolute value desc, then key asc.
    ranked = sorted(candidates, key=lambda driver: (-driver.absolute_delta.copy_abs(), driver.key))
    total_absolute_movement = sum(driver.absolute_delta.copy_abs() for driver in ranked)
    primary = ranked[:evidence_limit]
    primary_keys = {driver.key for driver in primary}

    outliers: list[VarianceDriver] = []
    for driver in ranked:
        if driver.key in primary_keys:
            continue
        if driver.relative_delta is None:
            continue
        if total_absolute_movement == 0:
            continue
        if driver.absolute_delta.copy_abs() / total_absolute_movement >= Decimal("0.05"):
            outliers.append(driver.model_copy(update={"is_outlier": True}))
        if len(outliers) >= 2:
            break

    return primary, outliers


def _breakdown_total_absolute_movement(
    current: DimensionBreakdown,
    baseline: DimensionBreakdown,
) -> Decimal:
    current_by_key = {bucket.key: bucket.value for bucket in current.buckets}
    baseline_by_key = {bucket.key: bucket.value for bucket in baseline.buckets}
    keys = current_by_key.keys() | baseline_by_key.keys()
    return sum(
        ((current_by_key.get(key, Decimal("0")) - baseline_by_key.get(key, Decimal("0"))).copy_abs() for key in keys),
        Decimal("0"),
    )


def combine_coverage_status(*statuses: CoverageStatus) -> CoverageStatus:
    """Combine coverage without downgrading an unavailable period to partial."""
    if any(status == CoverageStatus.UNAVAILABLE for status in statuses):
        return CoverageStatus.UNAVAILABLE
    if any(status == CoverageStatus.PARTIAL for status in statuses):
        return CoverageStatus.PARTIAL
    return CoverageStatus.COMPLETE


def _calculate_residual(
    metric_comparison: PeriodComparison,
    dimension_views: list[VarianceDimensionView],
) -> Decimal:
    """Residual = total metric change minus primary dimension's driver contributions."""
    if not dimension_views:
        return metric_comparison.absolute_delta
    return dimension_views[0].residual


def analyze_variance(
    current_dataset: AnalysisDataset,
    baseline_dataset: AnalysisDataset,
    spec: ComparisonSpec,
    *,
    language: str = "en",
) -> VarianceAnalysisResult:
    """Run a full variance analysis across measures and dimensions."""
    metric_spec = MetricSpec(
        metric=spec.metric,
        basis=spec.basis,
        dimensions=spec.dimensions,
        confidence_policy=spec.confidence_policy,
        completeness_policy=spec.completeness_policy,
    )

    current_metric = calculate_metric(current_dataset, metric_spec)
    baseline_metric = calculate_metric(baseline_dataset, metric_spec)
    metric_comparison = compare_periods(current_metric, baseline_metric)

    current_breakdowns = calculate_breakdowns(current_dataset, metric_spec, language=language)
    baseline_breakdowns = calculate_breakdowns(baseline_dataset, metric_spec, language=language)
    baseline_by_dimension = {breakdown.dimension: breakdown for breakdown in baseline_breakdowns}

    dimension_views: list[VarianceDimensionView] = []
    for current_breakdown in current_breakdowns:
        baseline_breakdown = baseline_by_dimension.get(current_breakdown.dimension)
        if baseline_breakdown is None:
            baseline_breakdown = DimensionBreakdown(
                dimension=current_breakdown.dimension,
                metric=spec.metric,
                basis=spec.basis,
                total=Decimal("0"),
                total_count=0,
                buckets=[],
            )
        primary, outliers = _build_variance_drivers(
            current_breakdown, baseline_breakdown, evidence_limit=spec.evidence_limit, language=language
        )
        drivers = primary + outliers
        total_movement = _breakdown_total_absolute_movement(current_breakdown, baseline_breakdown)
        residual = metric_comparison.absolute_delta - sum(driver.absolute_delta for driver in drivers)

        dimension_views.append(
            VarianceDimensionView(
                dimension=current_breakdown.dimension,
                metric=spec.metric,
                drivers=drivers,
                residual=residual,
                total_absolute_movement=total_movement,
            )
        )

    residual = _calculate_residual(metric_comparison, dimension_views)
    uncertainty = current_metric.uncertain_value + baseline_metric.uncertain_value
    excluded = current_metric.excluded_value + baseline_metric.excluded_value

    coverage = combine_coverage_status(current_dataset.coverage_status, baseline_dataset.coverage_status)
    available = current_metric.available and baseline_metric.available

    return VarianceAnalysisResult(
        measure=spec.metric.value,
        basis=spec.basis,
        current_period=current_dataset,
        baseline_period=baseline_dataset,
        metric_comparisons=[metric_comparison],
        dimension_views=dimension_views,
        residual=residual,
        uncertainty=uncertainty,
        coverage=coverage,
        unresolved_value=uncertainty,
        excluded_value=excluded,
        evidence_selectors=[],
        available=available,
        unavailable_reason="coverage_incomplete" if not available else None,
    )
