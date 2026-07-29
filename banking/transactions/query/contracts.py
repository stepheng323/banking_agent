"""Shared query presentation and continuation contracts.

These models are intentionally shared between the query domain and the
orchestrator context layer so follow-up state does not have to be re-derived
from rendered text.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class SelectionKind(str, Enum):
    """Kinds of selectable query entities."""

    TRANSACTION = "transaction"
    GROUP_BUCKET = "group_bucket"
    BENEFICIARY = "beneficiary"
    ACCOUNT = "account"
    REFERENT = "referent"
    SUMMARY_SCOPE = "summary_scope"


class SurfaceViewMode(str, Enum):
    """Typed surface modes presented by the query domain."""

    DIRECT_ANSWER = "direct_answer"
    TRANSACTION_LIST = "transaction_list"
    GROUPED_SUMMARY = "grouped_summary"
    CLARIFICATION = "clarification"
    INSIGHT = "insight"
    COMPOSITE = "composite"


class PresentationMode(str, Enum):
    """Typed rendering modes for final user copy."""

    DIRECT_ANSWER = "direct_answer"
    SUMMARY_LIST = "summary_list"
    TRANSACTION_LIST = "transaction_list"
    CLARIFY = "clarify"
    INSIGHT = "insight"
    COMPOSITE = "composite"


FactCapability = Literal[
    "date",
    "amount",
    "bank",
    "counterparty",
    "status",
    "description",
    "reference",
    "account",
    "direction",
    "category",
]


class InsightEvidenceSelectionBase(BaseModel):
    """Base analytical selector that can be compiled into evidence rows."""

    basis: Literal["ledger_transactions", "economic_events"]


class VarianceDriversEvidenceSelection(InsightEvidenceSelectionBase):
    insight_type: Literal["variance_drivers"] = "variance_drivers"
    measure: str
    dimension: Literal["category", "counterparty", "account", "event_type", "cash_flow_class"]
    bucket_key: str
    metric: Literal["spending", "income", "inflow", "outflow", "net_cash_flow"]
    current_start: str
    current_end: str
    baseline_start: str
    baseline_end: str


class ProbableDuplicatesEvidenceSelection(InsightEvidenceSelectionBase):
    insight_type: Literal["probable_duplicates"] = "probable_duplicates"
    duplicate_group_id: str
    effective_start: str | None = None
    effective_end: str | None = None
    transaction_ids: list[str] = Field(default_factory=list, max_length=20)


class RecurringPatternsEvidenceSelection(InsightEvidenceSelectionBase):
    insight_type: Literal["recurring_patterns"] = "recurring_patterns"
    series_id: str
    effective_start: str
    effective_end: str
    transaction_ids: list[str] = Field(default_factory=list, max_length=20)


class AnomaliesEvidenceSelection(InsightEvidenceSelectionBase):
    insight_type: Literal["anomalies"] = "anomalies"
    anomaly_id: str
    effective_start: str
    effective_end: str
    transaction_id: str


class CounterpartyConcentrationEvidenceSelection(InsightEvidenceSelectionBase):
    insight_type: Literal["counterparty_concentration"] = "counterparty_concentration"
    counterparty_key: str
    measure: Literal["spending", "income", "inflow", "outflow"]
    effective_start: str
    effective_end: str


class ForecastEvidenceSelection(InsightEvidenceSelectionBase):
    insight_type: Literal["forecast"] = "forecast"
    component_id: str


class RunwayEvidenceSelection(InsightEvidenceSelectionBase):
    insight_type: Literal["runway"] = "runway"
    assumption_id: str


class CashFlowQualityEvidenceSelection(InsightEvidenceSelectionBase):
    insight_type: Literal["cash_flow_quality"] = "cash_flow_quality"
    factor_id: str


InsightEvidenceSelection = Annotated[
    VarianceDriversEvidenceSelection
    | ProbableDuplicatesEvidenceSelection
    | RecurringPatternsEvidenceSelection
    | AnomaliesEvidenceSelection
    | CounterpartyConcentrationEvidenceSelection
    | ForecastEvidenceSelection
    | RunwayEvidenceSelection
    | CashFlowQualityEvidenceSelection,
    Field(discriminator="insight_type"),
]


class SelectionPayload(BaseModel):
    """Stable selection payload emitted by query surfaces."""

    selection_kind: Literal["transaction", "group_bucket", "beneficiary", "account", "referent", "summary_scope"]
    entity_type: str = "generic"
    entity_id: str | None = None
    label: str
    group_by: Literal["category", "merchant", "day", "account", "transaction_type"] | None = None
    group_key: str | None = None
    filters_patch: dict[str, Any] = Field(default_factory=dict)
    time_patch: dict[str, Any] | None = None
    fact_capabilities: list[FactCapability] = Field(default_factory=list)
    handoff_payload: dict[str, Any] | None = None
    insight_evidence: InsightEvidenceSelection | None = None


class FocusedReferent(BaseModel):
    """Shared short-term referent for follow-up and handoff flows."""

    referent_type: Literal["transaction", "beneficiary", "group_bucket"] = "beneficiary"
    label: str
    entity_id: str | None = None
    selection_payload: SelectionPayload | None = None
    recipient_name: str | None = None
    recipient_account: str | None = None
    recipient_bank_name: str | None = None
    recipient_bank_code: str | None = None
    recipient_resolved_name: str | None = None
    source: Literal["query"] = "query"


class SurfaceItemView(BaseModel):
    """Typed item record rendered within a surface view."""

    id: str
    label: str
    amount: float | None = None
    count: int | None = None
    payload: SelectionPayload
    metadata: dict[str, Any] = Field(default_factory=dict)


class SurfaceSection(BaseModel):
    """One deterministic section of a bounded multi-step query response."""

    step_id: str
    role: Literal["primary", "supporting", "evidence"]
    mode: SurfaceViewMode
    heading: str | None = None
    lead_text: str | None = None
    items: list[SurfaceItemView] = Field(default_factory=list)
    unavailable_reason: str | None = None


class SurfaceView(BaseModel):
    """Typed view over the current query result."""

    mode: SurfaceViewMode
    items: list[SurfaceItemView] = Field(default_factory=list)
    lead_text: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    sections: list[SurfaceSection] = Field(default_factory=list, max_length=3)


class PresentationPlan(BaseModel):
    """Pure rendering contract produced after query execution."""

    mode: PresentationMode
    heading: str | None = None
    lead_text: str | None = None
    evidence_lines: list[str] = Field(default_factory=list)
    items: list[str] = Field(default_factory=list)
    hint_text: str | None = None
    selection_payloads: list[SelectionPayload] = Field(default_factory=list)
