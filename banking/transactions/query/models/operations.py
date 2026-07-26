"""Authoritative semantic query operations.

This module contains the runtime-facing v2 query request.  Natural-language
extraction models live separately; execution and presentation consume only
the validated operations defined here.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, ClassVar, Literal, TypeAlias, Union, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from banking.transactions.query.contracts import InsightEvidenceSelection


class QueryModel(BaseModel):
    """Strict base model for query semantics."""

    model_config = ConfigDict(extra="forbid")


class Money(QueryModel):
    """A positive monetary value in the query domain."""

    amount: Decimal = Field(gt=0)
    currency: Literal["NGN"] = "NGN"


class ExactAmount(QueryModel):
    type: Literal["exact"] = "exact"
    value: Money


class ApproximateAmount(QueryModel):
    type: Literal["approximate"] = "approximate"
    value: Money
    tolerance_percent: Decimal = Field(default=Decimal("10"), gt=0, le=100)

    @property
    def minimum(self) -> Decimal:
        return self.value.amount * (Decimal("1") - self.tolerance_percent / Decimal("100"))

    @property
    def maximum(self) -> Decimal:
        return self.value.amount * (Decimal("1") + self.tolerance_percent / Decimal("100"))


class AmountRange(QueryModel):
    type: Literal["range"] = "range"
    minimum: Money | None = None
    maximum: Money | None = None
    minimum_inclusive: bool = True
    maximum_inclusive: bool = True

    @model_validator(mode="after")
    def validate_range(self) -> AmountRange:
        if self.minimum is None and self.maximum is None:
            raise ValueError("amount range requires a minimum or maximum")
        if self.minimum and self.maximum:
            if self.minimum.currency != self.maximum.currency:
                raise ValueError("amount range currencies must match")
            if self.minimum.amount > self.maximum.amount:
                raise ValueError("amount range minimum cannot exceed maximum")
        return self


AmountConstraint: TypeAlias = Annotated[
    ExactAmount | ApproximateAmount | AmountRange,
    Field(discriminator="type"),
]


class ResolvedPeriod(QueryModel):
    """Inclusive runtime date range."""

    start: date
    end: date
    timezone: Literal["Africa/Lagos"] = "Africa/Lagos"
    granularity: Literal["day", "week", "month"] | None = None

    @model_validator(mode="after")
    def validate_period(self) -> ResolvedPeriod:
        if self.start > self.end:
            raise ValueError("period start cannot be after end")
        return self


class AllAccounts(QueryModel):
    type: Literal["all"] = "all"


class NamedAccount(QueryModel):
    type: Literal["named"] = "named"
    name: str = Field(min_length=1)


class AccountById(QueryModel):
    type: Literal["id"] = "id"
    account_id: str = Field(min_length=1)


class SelectedAccounts(QueryModel):
    type: Literal["selected"] = "selected"
    account_ids: list[str] = Field(min_length=1)


class ContextualAccount(QueryModel):
    type: Literal["contextual"] = "contextual"
    frame_id: str | None = None
    entity_id: str | None = None


AccountSelector: TypeAlias = Annotated[
    AllAccounts | NamedAccount | AccountById | SelectedAccounts | ContextualAccount,
    Field(discriminator="type"),
]


class NamedCounterparty(QueryModel):
    type: Literal["named"] = "named"
    name: str = Field(min_length=1)


class UnspecifiedCounterparty(QueryModel):
    type: Literal["unspecified"] = "unspecified"
    entity_type: Literal["person", "merchant", "organization", "unknown"] = "unknown"


class ContextualCounterparty(QueryModel):
    type: Literal["contextual"] = "contextual"
    frame_id: str | None = None
    entity_id: str | None = None


class SavedBeneficiaryCounterparty(QueryModel):
    type: Literal["saved_beneficiary"] = "saved_beneficiary"
    beneficiary_id: str | None = None
    name: str | None = None

    @model_validator(mode="after")
    def validate_identity(self) -> SavedBeneficiaryCounterparty:
        if not self.beneficiary_id and not self.name:
            raise ValueError("saved beneficiary requires an id or name")
        return self


CounterpartyReference: TypeAlias = Annotated[
    NamedCounterparty | UnspecifiedCounterparty | ContextualCounterparty | SavedBeneficiaryCounterparty,
    Field(discriminator="type"),
]


class CounterpartySelector(QueryModel):
    role: Literal["sender", "recipient", "merchant", "any"]
    reference: CounterpartyReference


class TextMatch(QueryModel):
    query: str = Field(min_length=1)
    mode: Literal["keyword"] = "keyword"


class TransactionPredicate(QueryModel):
    """Composable transaction constraints; fields combine with AND."""

    amount: AmountConstraint | None = None
    direction: Literal["credit", "debit"] | None = None
    statuses: list[Literal["failed", "pending", "successful", "reversed"]] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    event_types: list[str] = Field(default_factory=list)
    counterparty: CounterpartySelector | None = None
    narration: TextMatch | None = None
    exclusions: list[str] = Field(default_factory=list)


class QueryScope(QueryModel):
    period: ResolvedPeriod
    accounts: AccountSelector = Field(default_factory=AllAccounts)
    predicate: TransactionPredicate = Field(default_factory=TransactionPredicate)


QueryFactField = Literal[
    "date",
    "counterparty",
    "amount",
    "bank",
    "status",
    "description",
    "reference",
    "account",
    "direction",
    "category",
]


class RetrieveProjection(QueryModel):
    shape: Literal["existence", "fact", "detail", "list"] = "list"
    fact_field: QueryFactField | None = None
    include: list[Literal["transaction", "counterparty"]] = Field(
        default_factory=lambda: cast(list[Literal["transaction", "counterparty"]], ["transaction"])
    )

    @model_validator(mode="after")
    def validate_fact_projection(self) -> RetrieveProjection:
        if self.shape == "fact" and self.fact_field is None:
            raise ValueError("fact projection requires fact_field")
        if self.shape != "fact" and self.fact_field is not None:
            raise ValueError("fact_field is only valid for fact projections")
        return self


class RetrieveSelection(QueryModel):
    cardinality: Literal["one", "many", "existence"] = "many"
    order: Literal["latest", "oldest", "largest", "smallest"] = "latest"
    order_explicit: bool = False
    limit: int | None = Field(default=None, ge=1, le=100)
    ambiguity_policy: Literal["show_candidates"] = "show_candidates"


class RetrieveOperation(QueryModel):
    kind: Literal["retrieve"] = "retrieve"
    scope: QueryScope
    projection: RetrieveProjection = Field(default_factory=RetrieveProjection)
    selection: RetrieveSelection = Field(default_factory=RetrieveSelection)


class ScalarSummarySpec(QueryModel):
    type: Literal["scalar"] = "scalar"
    measure: Literal["spending", "income", "net_cash_flow", "transactions"]
    statistic: Literal["sum", "count", "average", "largest", "smallest"]


class GroupedSummarySpec(QueryModel):
    type: Literal["grouped"] = "grouped"
    measure: Literal["spending", "income", "net_cash_flow", "transactions"]
    statistic: Literal["sum", "count", "average"] = "sum"
    dimension: Literal["category", "counterparty", "day", "account", "transaction_type"]
    rank_by: Literal["amount", "count"] = "amount"
    order: Literal["descending", "ascending"] = "descending"
    limit: int | None = Field(default=5, ge=1, le=100)
    answer_cardinality: Literal["one", "many"] = "many"


class CashFlowSummarySpec(QueryModel):
    type: Literal["cash_flow"] = "cash_flow"
    group_by: Literal["account"] | None = None


SummarySpec: TypeAlias = Annotated[
    ScalarSummarySpec | GroupedSummarySpec | CashFlowSummarySpec,
    Field(discriminator="type"),
]


class SummarizeOperation(QueryModel):
    kind: Literal["summarize"] = "summarize"
    scope: QueryScope
    summary: SummarySpec


class PreviousEquivalentBaseline(QueryModel):
    type: Literal["previous_equivalent"] = "previous_equivalent"


class YearAgoBaseline(QueryModel):
    type: Literal["year_ago"] = "year_ago"


class ExplicitBaseline(QueryModel):
    type: Literal["explicit"] = "explicit"
    period: ResolvedPeriod


ComparisonBaseline: TypeAlias = Annotated[
    PreviousEquivalentBaseline | YearAgoBaseline | ExplicitBaseline,
    Field(discriminator="type"),
]


class PeriodComparisonSpec(QueryModel):
    type: Literal["period"] = "period"
    baseline: ComparisonBaseline = Field(default_factory=PreviousEquivalentBaseline)
    measures: list[Literal["spending", "income", "net_cash_flow", "transactions"]] = Field(min_length=1)


class CompareOperation(QueryModel):
    kind: Literal["compare"] = "compare"
    scope: QueryScope
    comparison: PeriodComparisonSpec


class InsightSpecBase(QueryModel):
    insight_type: str
    analysis_basis: Literal["ledger_transactions", "economic_events"] = "economic_events"
    confidence_policy: Literal["include", "exclude_uncertain", "segment_uncertain"] = "segment_uncertain"
    completeness_policy: Literal["disclose", "require_complete"] = "disclose"
    evidence_limit: int = Field(default=5, ge=1, le=20)


class VarianceDriversSpec(InsightSpecBase):
    type: Literal["variance_drivers"] = "variance_drivers"
    insight_type: Literal["variance_drivers"] = "variance_drivers"
    baseline: ComparisonBaseline = Field(default_factory=PreviousEquivalentBaseline)
    measure: Literal["spending", "income", "net_cash_flow", "cash_flow_overview"] = "spending"
    dimensions: list[Literal["category", "counterparty", "account", "event_type", "cash_flow_class"]] = Field(
        default_factory=lambda: cast(
            list[Literal["category", "counterparty", "account", "event_type", "cash_flow_class"]],
            ["category", "counterparty"],
        )
    )
    evidence: InsightEvidenceSelection | None = None

    @model_validator(mode="after")
    def deduplicate_dimensions(self) -> VarianceDriversSpec:
        self.dimensions = list(dict.fromkeys(self.dimensions))
        return self


class ProbableDuplicatesSpec(InsightSpecBase):
    type: Literal["probable_duplicates"] = "probable_duplicates"
    insight_type: Literal["probable_duplicates"] = "probable_duplicates"
    min_confidence: float = Field(default=0.80)
    lookback_days: int = Field(default=90)
    evidence: InsightEvidenceSelection | None = None


class RecurringPatternsSpec(InsightSpecBase):
    type: Literal["recurring_patterns"] = "recurring_patterns"
    insight_type: Literal["recurring_patterns"] = "recurring_patterns"
    lookback_days: int = Field(default=180, ge=60, le=365)
    evidence: InsightEvidenceSelection | None = None


class AnomaliesSpec(InsightSpecBase):
    type: Literal["anomalies"] = "anomalies"
    insight_type: Literal["anomalies"] = "anomalies"
    baseline_days: int = Field(default=90)
    min_comparable_observations: int = Field(default=6)
    min_covered_days: int = Field(default=42)
    evidence: InsightEvidenceSelection | None = None


class CounterpartyConcentrationSpec(InsightSpecBase):
    type: Literal["counterparty_concentration"] = "counterparty_concentration"
    insight_type: Literal["counterparty_concentration"] = "counterparty_concentration"
    measure: Literal["spending", "income", "inflow", "outflow"] = "spending"
    evidence: InsightEvidenceSelection | None = None


class ForecastSpec(InsightSpecBase):
    type: Literal["forecast"] = "forecast"
    insight_type: Literal["forecast"] = "forecast"
    horizon_days: int = Field(default=30, ge=7, le=90)
    history_days: int = Field(default=180)
    evidence: InsightEvidenceSelection | None = None


class RunwaySpec(InsightSpecBase):
    type: Literal["runway"] = "runway"
    insight_type: Literal["runway"] = "runway"
    baseline_days: int = Field(default=90)
    evidence: InsightEvidenceSelection | None = None


class CashFlowQualitySpec(InsightSpecBase):
    type: Literal["cash_flow_quality"] = "cash_flow_quality"
    insight_type: Literal["cash_flow_quality"] = "cash_flow_quality"
    min_complete_months: int = Field(default=3)
    evidence: InsightEvidenceSelection | None = None


InsightSpec: TypeAlias = Annotated[
    Union[
        VarianceDriversSpec,
        ProbableDuplicatesSpec,
        RecurringPatternsSpec,
        AnomaliesSpec,
        CounterpartyConcentrationSpec,
        ForecastSpec,
        RunwaySpec,
        CashFlowQualitySpec,
    ],
    Field(discriminator="insight_type"),
]


class AnalyzeOperation(QueryModel):
    kind: Literal["analyze"] = "analyze"
    scope: QueryScope
    analysis: InsightSpec


class AffordabilitySpec(QueryModel):
    type: Literal["affordability"] = "affordability"
    amount: Money
    accounts: AccountSelector = Field(default_factory=AllAccounts)
    item_name: str | None = None


class AssessOperation(QueryModel):
    kind: Literal["assess"] = "assess"
    assessment: AffordabilitySpec


QueryOperation: TypeAlias = Annotated[
    RetrieveOperation | SummarizeOperation | CompareOperation | AnalyzeOperation | AssessOperation,
    Field(discriminator="kind"),
]


class QueryRequest(QueryModel):
    """The sole authoritative executable query representation."""

    schema_version: Literal[2] = 2
    timezone: Literal["Africa/Lagos"] = "Africa/Lagos"
    operation: QueryOperation

    @property
    def scope(self) -> QueryScope | None:
        """Return the operation scope when the operation is transaction-backed."""
        operation = self.operation
        if isinstance(operation, (RetrieveOperation, SummarizeOperation, CompareOperation, AnalyzeOperation)):
            return operation.scope
        return None

    @property
    def period(self) -> ResolvedPeriod | None:
        scope = self.scope
        return scope.period if scope is not None else None

    @property
    def accounts(self) -> AccountSelector:
        operation = self.operation
        if isinstance(operation, AssessOperation):
            return operation.assessment.accounts
        scope = self.scope
        return scope.accounts if scope is not None else AllAccounts()

    # Read-only projections used by presentation and continuation code.  They
    # deliberately derive from ``operation`` and are never serialized, so the
    # request remains the sole source of truth during the cutoff.
    @property
    def time_range(self) -> ResolvedPeriod | None:
        return self.period

    @property
    def time_start(self) -> date:
        period = self.period
        if period is None:
            raise ValueError("operation has no transaction period")
        return period.start

    @property
    def time_end(self) -> date:
        period = self.period
        if period is None:
            raise ValueError("operation has no transaction period")
        return period.end

    @property
    def accounts_scope(self) -> Literal["single", "all"]:
        return "all" if isinstance(self.accounts, AllAccounts) else "single"

    @property
    def account_name(self) -> str | None:
        return self.accounts.name if isinstance(self.accounts, NamedAccount) else None

    @property
    def result_limit(self) -> int | None:
        operation = self.operation
        if isinstance(operation, RetrieveOperation):
            return operation.selection.limit
        if isinstance(operation, SummarizeOperation) and isinstance(operation.summary, GroupedSummarySpec):
            return 1 if operation.summary.answer_cardinality == "one" else None
        if (
            isinstance(operation, SummarizeOperation)
            and isinstance(operation.summary, ScalarSummarySpec)
            and operation.summary.statistic in {"largest", "smallest"}
        ):
            return 1
        return None

    @property
    def result_reference(self) -> Literal["latest", "oldest"] | None:
        operation = self.operation
        if (
            isinstance(operation, RetrieveOperation)
            and operation.selection.order_explicit
            and operation.selection.order in {"latest", "oldest"}
        ):
            return cast(Literal["latest", "oldest"], operation.selection.order)
        return None

    @property
    def answer_fact_field(self) -> QueryFactField | None:
        operation = self.operation
        return operation.projection.fact_field if isinstance(operation, RetrieveOperation) else None

    @property
    def amount_check(self) -> float | None:
        operation = self.operation
        return float(operation.assessment.amount.amount) if isinstance(operation, AssessOperation) else None

    @property
    def item_name(self) -> str | None:
        operation = self.operation
        return operation.assessment.item_name if isinstance(operation, AssessOperation) else None

    @property
    def filters(self):
        """Compatibility projection for code that only reads normalized filters."""
        from banking.transactions.query.models.domain import Filters

        scope = self.scope
        if scope is None:
            return None
        predicate = scope.predicate
        counterparty = predicate.counterparty
        counterparty_values = None
        merchant_values = None
        if counterparty and isinstance(counterparty.reference, NamedCounterparty):
            counterparty_values = [counterparty.reference.name]
        if predicate.narration:
            merchant_values = [predicate.narration.query]
        minimum = maximum = None
        min_inclusive = max_inclusive = True
        amount = predicate.amount
        if isinstance(amount, ExactAmount):
            minimum = maximum = float(amount.value.amount)
        elif isinstance(amount, ApproximateAmount):
            minimum, maximum = float(amount.minimum), float(amount.maximum)
        elif isinstance(amount, AmountRange):
            minimum = float(amount.minimum.amount) if amount.minimum else None
            maximum = float(amount.maximum.amount) if amount.maximum else None
            min_inclusive, max_inclusive = amount.minimum_inclusive, amount.maximum_inclusive
        return Filters(
            category=predicate.categories or None,
            merchant=merchant_values,
            counterparty=counterparty_values,
            min_amount=minimum,
            max_amount=maximum,
            min_amount_inclusive=min_inclusive,
            max_amount_inclusive=max_inclusive,
            transaction_type=predicate.direction,
            status=predicate.statuses[0] if predicate.statuses else None,
            exclude=predicate.exclusions or None,
            account_filter=self.account_name,
        )

    @property
    def aggregation(self):
        from banking.transactions.query.models.domain import Aggregation

        operation = self.operation
        if not isinstance(operation, SummarizeOperation):
            return None
        summary = operation.summary
        if isinstance(summary, CashFlowSummarySpec):
            return Aggregation(
                type="breakdown" if summary.group_by == "account" else "sum",
                group_by=summary.group_by,
            )
        if isinstance(summary, GroupedSummarySpec):
            group_by = "merchant" if summary.dimension == "counterparty" else summary.dimension
            return Aggregation(
                type=(
                    summary.statistic
                    if summary.dimension == "counterparty" or summary.statistic == "count"
                    else "breakdown"
                ),
                group_by=cast(
                    Literal["category", "merchant", "day", "account", "transaction_type"],
                    group_by,
                ),
                limit=summary.limit,
                sort_by=summary.rank_by,
            )
        return Aggregation(
            type=summary.statistic,
            limit=1 if summary.statistic in {"largest", "smallest"} else 5,
        )

    @property
    def intent(self):
        from banking.transactions.query.models.domain import QueryIntent

        operation = self.operation
        if isinstance(operation, RetrieveOperation):
            return (
                QueryIntent.TRANSACTION_DETAIL
                if operation.projection.shape in {"fact", "detail"}
                else QueryIntent.TRANSACTION_LIST
            )
        if isinstance(operation, SummarizeOperation):
            if isinstance(operation.summary, CashFlowSummarySpec):
                return QueryIntent.CASH_FLOW_SUMMARY
            if isinstance(operation.summary, GroupedSummarySpec) and operation.summary.dimension == "counterparty":
                return QueryIntent.BENEFICIARY_SUMMARY
            return QueryIntent.ANALYTICS_SUMMARY
        if isinstance(operation, CompareOperation):
            return QueryIntent.TIME_COMPARISON
        if isinstance(operation, AnalyzeOperation):
            return QueryIntent.INSIGHT
        return QueryIntent.AFFORDABILITY

    @property
    def request_shape(self) -> str:
        operation = self.operation
        if isinstance(operation, RetrieveOperation):
            return operation.projection.shape
        if isinstance(operation, CompareOperation):
            return "comparison"
        if isinstance(operation, AnalyzeOperation):
            return "insight"
        if isinstance(operation, AssessOperation):
            return "affordability"
        return "analytics"

    analysis_type: ClassVar[str] = "immediate"
    continuation_type: ClassVar[None] = None
    continuation_delta_type: ClassVar[None] = None
    conversational_prefix: ClassVar[None] = None
    intent_spec: ClassVar[Any | None] = None
    execution_plan: ClassVar[Any | None] = None


class CoverageReport(QueryModel):
    complete: bool = True
    requested_account_count: int | None = Field(default=None, ge=0)
    covered_account_count: int | None = Field(default=None, ge=0)
    missing_account_ids: list[str] = Field(default_factory=list)
    period_clamped: bool = False
    results_truncated: bool = False
    notices: list[str] = Field(default_factory=list)
