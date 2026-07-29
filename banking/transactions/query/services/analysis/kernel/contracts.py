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
    # Requested dates describe the query; coverage is tracked separately so
    # predictive insights never mistake a requested window for observed data.
    requested_days: int | None = None
    fully_covered_days: int | None = None
    covered_account_count: int = 0
    requested_account_count: int = 0
    missing_account_gaps: dict[str, list[tuple[Any, Any]]] = Field(default_factory=dict)
    unresolved_count: int = 0
    unresolved_value: Decimal = Decimal("0")

    model_config = ConfigDict(frozen=True)

    def model_post_init(self, __context: Any) -> None:
        """Keep direct test/fixture construction meaningful during cutover."""
        del __context
        if self.requested_days is not None and self.fully_covered_days is not None:
            return
        try:
            requested_days = max((self.end_date - self.start_date).days + 1, 0)
        except Exception:
            requested_days = 0
        # Directly constructed fixtures predate explicit coverage windows. They
        # retain the requested duration unless they opt into a supplied
        # fully_covered_days value; production sources always set it exactly.
        fully_covered_days = requested_days
        if self.requested_days is None:
            object.__setattr__(self, "requested_days", requested_days)
        if self.fully_covered_days is None:
            object.__setattr__(self, "fully_covered_days", fully_covered_days)


class InsightResultMetadata(BaseModel):
    """Shared truth and coverage metadata for deterministic insights."""

    basis: AnalysisBasis
    effective_start: Any
    effective_end: Any
    coverage: CoverageStatus
    included_count: int = 0
    included_value: Decimal = Decimal("0")
    excluded_count: int = 0
    excluded_value: Decimal = Decimal("0")
    uncertain_count: int = 0
    uncertain_value: Decimal = Decimal("0")
    available: bool = True
    unavailable_reason: str | None = None

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
    metadata: InsightResultMetadata | None = None

    model_config = ConfigDict(frozen=True)


class DuplicateCandidateGroup(BaseModel):
    """A cluster of transactions that are likely duplicates."""

    group_id: str
    confidence: float
    transactions: list[dict[str, Any]]
    redundant_value: Decimal
    metric: AnalysisMetric

    model_config = ConfigDict(frozen=True)


class ProbableDuplicatesResult(BaseModel):
    """Result of probable duplicates detection."""

    basis: AnalysisBasis
    dataset: AnalysisDataset
    candidates: list[DuplicateCandidateGroup] = Field(default_factory=list)
    total_redundant_value: Decimal = Decimal("0")
    coverage: CoverageStatus
    available: bool = True
    unavailable_reason: Literal["coverage_incomplete"] | None = None
    metadata: InsightResultMetadata | None = None

    model_config = ConfigDict(frozen=True)


class RecurringSeries(BaseModel):
    series_id: str
    counterparty: str
    transaction_type: str
    frequency: Literal["weekly", "monthly"]
    average_amount: Decimal
    median_amount: Decimal = Decimal("0")
    confidence: float = 0.0
    transaction_count: int
    transactions: list[dict[str, Any]] = Field(default_factory=list)


class RecurringPatternsResult(BaseModel):
    available: bool = True
    dataset: AnalysisDataset | None = None
    series: list[RecurringSeries] = Field(default_factory=list)
    metadata: InsightResultMetadata | None = None
    model_config = ConfigDict(frozen=True)


class AnomalyCandidate(BaseModel):
    anomaly_id: str
    transaction: dict[str, Any]
    baseline_average: Decimal
    multiplier: float
    robust_score: float = 0.0
    metric: Literal["spending", "income"] = "spending"
    dimension_name: str
    dimension_value: str


class AnomaliesResult(BaseModel):
    available: bool = True
    dataset: AnalysisDataset | None = None
    anomalies: list[AnomalyCandidate] = Field(default_factory=list)
    metadata: InsightResultMetadata | None = None
    model_config = ConfigDict(frozen=True)


class ConcentrationGroup(BaseModel):
    key: str = ""
    counterparty: str
    value: Decimal
    percentage: Decimal
    transactions: list[dict[str, Any]] = Field(default_factory=list)


class CounterpartyConcentrationResult(BaseModel):
    available: bool = True
    dataset: AnalysisDataset | None = None
    measure: Literal["spending", "income", "inflow", "outflow"] | None = None
    groups: list[ConcentrationGroup] = Field(default_factory=list)
    total_value: Decimal = Decimal("0")
    unresolved_value: Decimal = Decimal("0")
    residual_percentage: Decimal = Decimal("0")
    metadata: InsightResultMetadata | None = None
    model_config = ConfigDict(frozen=True)


class ForecastResult(BaseModel):
    available: bool = False
    unavailable_reason: str | None = None
    average_daily_spend: Decimal = Decimal(0)
    projected_spend: Decimal = Decimal(0)
    horizon_days: int = 30
    observed_days: int = 0
    metadata: InsightResultMetadata | None = None
    model_config = ConfigDict(frozen=True)


class RunwayResult(BaseModel):
    available: bool = False
    unavailable_reason: str | None = None
    average_burn_rate: Decimal = Decimal(0)
    current_balance: Decimal = Decimal(0)
    runway_days: int | None = None
    status: Literal["unavailable", "depleted", "bounded", "unbounded"] = "unavailable"
    metadata: InsightResultMetadata | None = None
    model_config = ConfigDict(frozen=True)


class CashFlowQualityResult(BaseModel):
    available: bool = False
    unavailable_reason: str | None = None
    inflow_outflow_ratio: Decimal = Decimal(0)
    is_healthy: bool = False
    analyzed_months: int = 0
    total_income: Decimal = Decimal("0")
    total_spending: Decimal = Decimal("0")
    net_cash_flow: Decimal = Decimal("0")
    metadata: InsightResultMetadata | None = None
    model_config = ConfigDict(frozen=True)
