"""Shared financial-analysis kernel primitives.

These types describe the *shared financial truth* across query intents.
Individual intents (analytics, cash-flow, time-comparison, insight) consume
these results and add their own conversational rendering.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AnalysisBasis = Literal["ledger_transactions", "economic_events"]


class AnalysisMetric(str, Enum):
    """A canonical financial metric the kernel can compute."""

    SPENDING = "spending"  # eligible outflows
    INCOME = "income"  # eligible inflows
    INFLOW = "inflow"  # raw settled credits
    OUTFLOW = "outflow"  # raw settled debits
    NET_CASH_FLOW = "net_cash_flow"  # eligible income minus eligible spending


class Dimension(str, Enum):
    """Dimensions along which a metric can be broken down."""

    CATEGORY = "category"
    COUNTERPARTY = "counterparty"
    ACCOUNT = "account"
    EVENT_TYPE = "event_type"
    CASH_FLOW_CLASS = "cash_flow_class"


class CoverageStatus(str, Enum):
    """Completeness of the underlying data for a metric."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class MetricResult(BaseModel):
    """A single metric value for a period, with inclusion/exclusion detail."""

    metric: AnalysisMetric
    basis: AnalysisBasis
    value: Decimal
    count: int = 0
    included_value: Decimal
    excluded_value: Decimal
    excluded_count: int = 0
    uncertain_value: Decimal
    uncertain_count: int = 0
    coverage_status: CoverageStatus
    completeness_policy: Literal["disclose", "require_complete"] = "disclose"
    available: bool = True
    unavailable_reason: Literal["coverage_incomplete"] | None = None

    model_config = ConfigDict(frozen=True)


class CashFlowAnalysis(BaseModel):
    """Canonical ledger/economic cash-flow truth for one period."""

    basis: AnalysisBasis
    inflow: MetricResult
    outflow: MetricResult
    net_cash_flow: MetricResult
    settled_rows: list[dict[str, Any]] = Field(default_factory=list)
    excluded_internal_count: int = 0
    excluded_unsettled_count: int = 0

    model_config = ConfigDict(frozen=True)


class Bucket(BaseModel):
    """A single dimensional bucket within a metric breakdown."""

    key: str
    label: str | None = None
    value: Decimal
    count: int = 0
    baseline_value: Decimal | None = None
    baseline_count: int | None = None

    model_config = ConfigDict(frozen=True)


class DimensionBreakdown(BaseModel):
    """Metric value broken down by one dimension."""

    dimension: Dimension
    metric: AnalysisMetric
    basis: AnalysisBasis
    total: Decimal
    total_count: int = 0
    buckets: list[Bucket] = Field(default_factory=list)
    unresolved_bucket: Bucket | None = None
    excluded_value: Decimal = Decimal("0")
    excluded_count: int = 0
    uncertain_value: Decimal = Decimal("0")
    uncertain_count: int = 0

    model_config = ConfigDict(frozen=True)


class PeriodComparison(BaseModel):
    """Comparison of a metric across two periods."""

    metric: AnalysisMetric
    basis: AnalysisBasis
    current_value: Decimal
    current_count: int
    baseline_value: Decimal
    baseline_count: int
    absolute_delta: Decimal
    relative_delta: Decimal | None = None
    relative_status: Literal["increased", "decreased", "unchanged", "new", "ended"] = "unchanged"

    model_config = ConfigDict(frozen=True)


class AnalysisDataset(BaseModel):
    """Raw dataset for a period with coverage metadata."""

    basis: AnalysisBasis
    period_label: str
    start_date: Any  # date
    end_date: Any  # date
    rows: list[dict[str, Any]] = Field(default_factory=list)
    coverage_status: CoverageStatus = CoverageStatus.UNAVAILABLE
    missing_accounts: list[str] = Field(default_factory=list)
    unresolved_count: int = 0
    unresolved_value: Decimal = Decimal("0")

    model_config = ConfigDict(frozen=True)


class ConfidencePolicy(str, Enum):
    """How to handle semantically uncertain rows."""

    INCLUDE = "include"
    EXCLUDE_UNCERTAIN = "exclude_uncertain"
    SEGMENT_UNCERTAIN = "segment_uncertain"


class MetricSpec(BaseModel):
    """Specification for a metric calculation."""

    metric: AnalysisMetric
    basis: AnalysisBasis
    dimensions: list[Dimension] = Field(default_factory=list)
    confidence_policy: ConfidencePolicy = ConfidencePolicy.SEGMENT_UNCERTAIN
    completeness_policy: Literal["disclose", "require_complete"] = "disclose"
    exclude_internal: bool = True
    exclude_financing_investing_for_operating: bool = True

    model_config = ConfigDict(frozen=True)


class ComparisonSpec(BaseModel):
    """Specification for a period comparison."""

    metric: AnalysisMetric
    basis: AnalysisBasis
    current: AnalysisDataset
    baseline: AnalysisDataset
    dimensions: list[Dimension] = Field(default_factory=list)
    confidence_policy: ConfidencePolicy = ConfidencePolicy.SEGMENT_UNCERTAIN
    completeness_policy: Literal["disclose", "require_complete"] = "disclose"
    evidence_limit: int = Field(default=5, ge=1, le=20)

    model_config = ConfigDict(frozen=True)


class VarianceDriver(BaseModel):
    """A dimensional bucket that explains part of a metric change."""

    dimension: Dimension
    key: str
    label: str | None = None
    current_value: Decimal
    baseline_value: Decimal
    absolute_delta: Decimal
    relative_delta: Decimal | None = None
    relative_status: Literal["increased", "decreased", "unchanged", "new", "ended"] = "unchanged"
    is_outlier: bool = False

    model_config = ConfigDict(frozen=True)


class VarianceDimensionView(BaseModel):
    """Drivers ranked within one dimension for a metric comparison."""

    dimension: Dimension
    metric: AnalysisMetric
    drivers: list[VarianceDriver] = Field(default_factory=list)
    residual: Decimal
    total_absolute_movement: Decimal

    model_config = ConfigDict(frozen=True)


class InsightEvidenceSelector(BaseModel):
    """Typed selector for drilling into a variance driver."""

    measure: str
    dimension: Dimension
    bucket_key: str
    basis: AnalysisBasis
    current_start: Any  # date
    current_end: Any  # date
    baseline_start: Any  # date
    baseline_end: Any  # date
    metric: AnalysisMetric
    filters: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)


class VarianceAnalysisResult(BaseModel):
    """Complete variance analysis across measures and dimensions."""

    measure: str
    basis: AnalysisBasis
    current_period: AnalysisDataset
    baseline_period: AnalysisDataset
    metric_comparisons: list[PeriodComparison] = Field(default_factory=list)
    dimension_views: list[VarianceDimensionView] = Field(default_factory=list)
    residual: Decimal
    uncertainty: Decimal
    coverage: CoverageStatus
    unresolved_value: Decimal
    excluded_value: Decimal
    evidence_selectors: list[InsightEvidenceSelector] = Field(default_factory=list)
    available: bool = True
    unavailable_reason: Literal["coverage_incomplete"] | None = None

    model_config = ConfigDict(frozen=True)
