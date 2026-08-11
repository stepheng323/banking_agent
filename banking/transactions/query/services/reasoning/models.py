"""Contracts for the query semantic reasoner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from banking.transactions.query.contracts import SurfaceView
from banking.transactions.query.models.conversation import PendingFieldClarification, QueryFocus, QueryScopeDelta
from banking.transactions.query.models.domain import (
    Filters,
    QueryFactField,
    QueryFrame,
    QueryIntent,
    QueryRequest,
    QueryResultItem,
    TimeRange,
)
from banking.transactions.query.models.extraction import (
    ClarificationPatch,
    FactQueryKind,
    QueryAggregation,
    QueryComparison,
    QueryExtractionResult,
    QueryFilters,
    QueryPlanDraft,
    QueryRequestShape,
    QueryTimeRange,
    ReasonerQueryExtraction,
)
from shared.types.query_preferences import QueryPreferenceUpdate

DecisionType = Literal[
    "fresh_query",
    "clarification_answer",
    "reinterpret_query",
    "continuation",
    "new_query",
    "end_session",
]

ContinuationType = Literal[
    "show_more",
    "show_evidence",
    "grouped_total_followup",
    "time_delta",
    "filter_delta",
    "expand",
    "conversational",
    "coverage",
    "explain_aggregate_scope",
    "drill_down",
    "recipient_drill_down",
    "aggregate",
    "unclear",
    "recheck",
    "reconcile",
    "repair",
    "update_preferences",
]

FollowupIntentType = Literal["refine_existing", "replace_scope", "continue_pagination", "previous_pagination", "none"]

AnswerModeType = Literal["memory_answer", "grounded_query", "ask_clarify"]

GroundedOperationType = Literal["compare_frames", "select_frame", "show_transactions", "reuse_frame"]

DeltaType = Literal["filter", "time", "limit", "reference", "none"]

ResultReferenceType = Literal["latest", "oldest"]

DrillDownActionType = Literal["view_details", "get_receipt", "report_issue", "re_transfer", "answer_fact"]

EndSessionKindType = Literal["courtesy", "dismissive", "generic"]

FactFieldType = Literal[
    "status",
    "amount",
    "recipient",
    "counterparty",
    "bank",
    "date",
    "description",
    "reference",
    "account",
    "direction",
    "category",
]
QueryTargetFieldType = FactFieldType
QueryRankType = Literal["largest", "smallest", "newest", "oldest"]
PageDirectionType = Literal["next", "previous"]
CoverageIntentType = Literal["result_completeness", "data_coverage", "ambiguous"]
TransactionDirectionDeltaType = Literal["credit", "debit", "both"]

SemanticContextModeType = Literal["none", "pending_clarification", "active_result"]

ReasonerSchemaType = Literal["active_continuation", "pending_clarification"]
ReasonerPromptProfileType = Literal[
    "repair",
    "focused_item",
    "transaction_list",
    "grouped_summary",
    "insight",
    "composite",
    "historical_frames",
    "pending_clarification",
]


def _strip_llm_schema_annotations(schema: dict[str, Any]) -> None:
    root_title = schema.get("title")

    def strip(value: Any) -> None:
        if isinstance(value, dict):
            value.pop("title", None)
            value.pop("description", None)
            value.pop("default", None)
            for child in value.values():
                strip(child)
        elif isinstance(value, list):
            for child in value:
                strip(child)

    strip(schema)
    if isinstance(root_title, str) and root_title:
        schema["title"] = root_title


class QuerySemanticDecision(BaseModel):
    """Unified semantic reasoner output for query turns."""

    decision: DecisionType
    confidence: float | None = Field(default=None)
    reason: str | None = Field(default=None)

    extraction: QueryExtractionResult | None = Field(default=None)
    time_period: str | None = Field(default=None)
    clarification_patch: ClarificationPatch | None = Field(default=None)
    repair_delta: QueryScopeDelta | None = Field(default=None)
    alternate_repair_delta: QueryScopeDelta | None = Field(default=None)
    plan: QueryPlanDraft | None = Field(default=None)
    preferences_update: QueryPreferenceUpdate | None = Field(default=None)

    continuation_type: ContinuationType | None = Field(default=None)
    followup_intent: FollowupIntentType | None = Field(default=None)
    answer_mode: AnswerModeType | None = Field(default=None)
    referenced_frame_ids: list[str] | None = Field(default=None)
    grounded_operation: GroundedOperationType | None = Field(default=None)
    delta_type: DeltaType | None = Field(default=None)
    time_range: TimeRange | None = Field(default=None)
    filters: Filters | None = Field(default=None)
    result_limit: int | None = Field(default=None)
    result_reference: ResultReferenceType | None = Field(default=None)
    target_text: str | None = Field(default=None)
    target_amount: float | None = Field(default=None)
    target_index: int | None = Field(default=None)
    target_step_id: str | None = Field(default=None)
    requested_field: QueryTargetFieldType | None = Field(default=None)
    rank: QueryRankType | None = Field(default=None)
    page_direction: PageDirectionType | None = Field(default=None)
    coverage_intent: CoverageIntentType | None = Field(default=None)
    transaction_direction_delta: TransactionDirectionDeltaType | None = Field(default=None)
    drill_down_index: int | None = Field(default=None)
    drill_down_action: DrillDownActionType | None = Field(default=None)
    recipient_name: str | None = Field(default=None)
    end_session_response: str | None = Field(default=None)
    end_session_kind: EndSessionKindType | None = Field(default=None)
    fact_field: FactFieldType | None = Field(default=None)
    response_text: str | None = Field(default=None)
    contextual_hint: str | None = Field(default=None)
    semantic_context_mode: SemanticContextModeType | None = Field(default=None)
    semantic_llm_used: bool | None = Field(default=None)
    deterministic_surface_action: str | None = Field(default=None)
    semantic_reasoner_schema: ReasonerSchemaType | None = Field(default=None)
    llm_calls_used: int | None = Field(default=None)
    single_llm_invariant: bool | None = Field(default=None)


class ActiveContinuationDecision(BaseModel):
    """Structured output for active-result turns."""

    decision: Literal["continuation", "fresh_query", "reinterpret_query", "new_query", "end_session"]
    confidence: float | None = Field(default=None)
    reason: str | None = Field(default=None)
    extraction: ReasonerQueryExtraction | None = Field(default=None)
    repair_delta: QueryScopeDelta | None = Field(default=None)
    alternate_repair_delta: QueryScopeDelta | None = Field(default=None)
    time_period: str | None = Field(default=None)
    continuation_type: ContinuationType | None = Field(default=None)
    followup_intent: FollowupIntentType | None = Field(default=None)
    answer_mode: AnswerModeType | None = Field(default=None)
    referenced_frame_ids: list[str] | None = Field(default=None)
    grounded_operation: GroundedOperationType | None = Field(default=None)
    delta_type: DeltaType | None = Field(default=None)
    time_range: TimeRange | None = Field(default=None)
    filters: Filters | None = Field(default=None)
    result_limit: int | None = Field(default=None)
    result_reference: ResultReferenceType | None = Field(default=None)
    target_text: str | None = Field(default=None)
    target_amount: float | None = Field(default=None)
    target_index: int | None = Field(default=None)
    requested_field: QueryTargetFieldType | None = Field(default=None)
    rank: QueryRankType | None = Field(default=None)
    page_direction: PageDirectionType | None = Field(default=None)
    coverage_intent: CoverageIntentType | None = Field(default=None)
    transaction_direction_delta: TransactionDirectionDeltaType | None = Field(default=None)
    drill_down_index: int | None = Field(default=None)
    drill_down_action: DrillDownActionType | None = Field(default=None)
    recipient_name: str | None = Field(default=None)
    end_session_response: str | None = Field(default=None)
    end_session_kind: EndSessionKindType | None = Field(default=None)
    fact_field: FactFieldType | None = Field(default=None)
    response_text: str | None = Field(default=None)
    contextual_hint: str | None = Field(default=None)

    def to_public_decision(self) -> QuerySemanticDecision:
        extraction = self.extraction.to_query_extraction_result() if self.extraction is not None else None
        return QuerySemanticDecision(
            decision=self.decision,
            confidence=self.confidence,
            reason=self.reason,
            extraction=extraction,
            repair_delta=self.repair_delta,
            alternate_repair_delta=self.alternate_repair_delta,
            time_period=self.time_period,
            continuation_type=self.continuation_type,
            followup_intent=self.followup_intent,
            answer_mode=self.answer_mode,
            referenced_frame_ids=self.referenced_frame_ids,
            grounded_operation=self.grounded_operation,
            delta_type=self.delta_type,
            time_range=self.time_range,
            filters=self.filters,
            result_limit=self.result_limit,
            result_reference=self.result_reference,
            target_text=self.target_text,
            target_amount=self.target_amount,
            target_index=self.target_index,
            requested_field=self.requested_field,
            rank=self.rank,
            page_direction=self.page_direction,
            coverage_intent=self.coverage_intent,
            transaction_direction_delta=self.transaction_direction_delta,
            drill_down_index=self.drill_down_index,
            drill_down_action=self.drill_down_action,
            recipient_name=self.recipient_name,
            end_session_response=self.end_session_response,
            end_session_kind=self.end_session_kind,
            fact_field=self.fact_field,
            response_text=self.response_text,
            contextual_hint=self.contextual_hint,
        )


class ActiveReasonerExtraction(BaseModel):
    """Compact non-insight extraction used only by active list/summary turns.

    Fresh parsing owns the larger insight and multi-step extraction vocabulary.
    Active list and summary continuations only need the ordinary transaction
    fields below; excluding insight specifications from their provider schema
    avoids paying for unrelated analytical contracts on every follow-up.
    """

    model_config = ConfigDict(extra="ignore", json_schema_extra=_strip_llm_schema_annotations)

    intent: QueryIntent = QueryIntent.TRANSACTION_LIST
    filters: QueryFilters = Field(default_factory=QueryFilters)
    time_range: QueryTimeRange = Field(default_factory=QueryTimeRange)
    comparison: QueryComparison | None = None
    aggregation: QueryAggregation | None = None
    request_shape: QueryRequestShape | None = None
    fact_query_kind: FactQueryKind | None = None
    result_limit: int | None = Field(default=None, ge=1, le=100)
    result_reference: Literal["latest", "oldest"] | None = None
    answer_fact_field: QueryFactField | None = None
    use_default_account_scope: bool = False

    @field_validator("request_shape", mode="before")
    @classmethod
    def _normalize_legacy_summary_shape(cls, value: Any) -> Any:
        """Accept the parser-era ``summary`` label at the LLM boundary.

        The canonical query contract has always called this surface
        ``grouped_summary``.  Older prompt bundles and provider responses can
        still emit ``summary`` during an active follow-up; normalizing it here
        keeps the reasoner on its single-call path instead of turning a valid
        refinement into a fresh-query fallback.
        """
        if value == "summary":
            return QueryRequestShape.GROUPED_SUMMARY
        return value

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = super().model_json_schema(*args, **kwargs)
        _strip_llm_schema_annotations(schema)
        return schema

    def to_query_extraction_result(self) -> QueryExtractionResult:
        return ReasonerQueryExtraction.model_validate(self.model_dump()).to_query_extraction_result()


class _NarrowActiveDecision(BaseModel):
    """Common fields retained by every surface-specific LLM contract."""

    # These are provider-facing adapters, not persisted query contracts.  A
    # provider can occasionally echo a legacy parser-only field even though
    # the generated narrow schema excludes it.  Discard unknown optional
    # fields so an otherwise grounded decision does not erase the active
    # query session and fall through to a fresh query.
    model_config = ConfigDict(extra="ignore", json_schema_extra=_strip_llm_schema_annotations)

    decision: Literal["continuation", "fresh_query", "reinterpret_query", "new_query", "end_session"]
    confidence: float | None = None
    continuation_type: ContinuationType | None = None
    followup_intent: FollowupIntentType | None = None
    response_text: str | None = None
    end_session_response: str | None = None
    end_session_kind: EndSessionKindType | None = None
    preferences_update: QueryPreferenceUpdate | None = None

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = super().model_json_schema(*args, **kwargs)
        _strip_llm_schema_annotations(schema)
        return schema

    def to_public_decision(self) -> QuerySemanticDecision:
        payload = self.model_dump(exclude_none=True)
        extraction = getattr(self, "extraction", None)
        if extraction is not None:
            payload["extraction"] = extraction.to_query_extraction_result()
        return QuerySemanticDecision.model_validate(payload)


class RepairDecision(BaseModel):
    """Sparse correction contract selected only by an advisory repair signal.

    Keeping the canonical scope delta out of ordinary list and summary
    schemas preserves their latency budget.  The signal chooses this schema;
    it never decides that a turn is a repair.  The model must still return
    ``continuation_type=repair`` (or another valid interpretation), and the
    runtime validates the delta against the focused source contract.
    """

    model_config = ConfigDict(extra="ignore", json_schema_extra=_strip_llm_schema_annotations)

    decision: Literal["continuation"]
    confidence: float = Field(ge=0.0, le=1.0)
    continuation_type: Literal["repair", "reconcile"]
    followup_intent: Literal["refine_existing", "replace_scope"]
    repair_delta: QueryScopeDelta
    alternate_repair_delta: QueryScopeDelta | None = None
    referenced_frame_ids: list[str] | None = None
    target_text: str | None = None
    target_amount: float | None = None
    target_step_id: str | None = None

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = super().model_json_schema(*args, **kwargs)
        _strip_llm_schema_annotations(schema)
        return schema

    def to_public_decision(self) -> QuerySemanticDecision:
        return QuerySemanticDecision.model_validate(self.model_dump(exclude_none=True))


class FocusedItemDecision(_NarrowActiveDecision):
    """Fact and drill-down decisions for one focused result."""

    # A focused result can be interrupted by a complete, standalone query
    # (for example, "Did I pay for Uber last month?").  Keep the ordinary
    # transaction extraction in this narrow adapter so the active reasoner
    # can hand the replacement directly to the compiler instead of emitting
    # ``new_query`` without the extraction that the runtime requires.
    extraction: ActiveReasonerExtraction | None = None
    drill_down_index: int | None = None
    drill_down_action: DrillDownActionType | None = None
    fact_field: FactFieldType | None = None
    requested_field: QueryTargetFieldType | None = None
    target_text: str | None = None
    target_amount: float | None = None
    answer_mode: AnswerModeType | None = None
    delta_type: DeltaType | None = None
    transaction_direction_delta: TransactionDirectionDeltaType | None = None
    time_range: TimeRange | None = None
    filters: Filters | None = None
    # A recipient change is a scoped fact follow-up (for example,
    # "what about Mum?").  Keep it in the focused-item schema so the narrow
    # provider contract does not silently discard the semantic delta before
    # the deterministic continuation compiler sees it.
    recipient_name: str | None = None

    @model_validator(mode="after")
    def _derive_direction_delta_from_typed_extraction(self) -> FocusedItemDecision:
        """Adapt a narrow extraction into the explicit continuation patch.

        Some provider responses express an income/spending switch through the
        extraction's typed transaction filter while omitting the optional
        convenience field.  Promote that already-typed value here so the
        continuation compiler applies only the direction change and preserves
        the active period/account scope.  This is contract adaptation, not
        interpretation of user wording.
        """
        if self.transaction_direction_delta is None and self.continuation_type in {"filter_delta", "aggregate"}:
            transaction_type = getattr(self.extraction.filters, "transaction_type", None) if self.extraction else None
            if transaction_type in {"credit", "debit"}:
                self.transaction_direction_delta = transaction_type
        return self


class TransactionListDecision(_NarrowActiveDecision):
    """Selection, pagination, aggregation, coverage, and refinement over a visible list."""

    extraction: ActiveReasonerExtraction | None = None
    target_index: int | None = None
    target_amount: float | None = None
    target_text: str | None = None
    requested_field: QueryTargetFieldType | None = None
    rank: QueryRankType | None = None
    page_direction: PageDirectionType | None = None
    coverage_intent: CoverageIntentType | None = None
    drill_down_index: int | None = None
    drill_down_action: DrillDownActionType | None = None
    fact_field: FactFieldType | None = None
    recipient_name: str | None = None


class CompositeDecision(TransactionListDecision):
    """A follow-up targeting one visible section of a multi-step answer."""

    target_step_id: str | None = None
    grounded_operation: GroundedOperationType | None = None
    repair_delta: QueryScopeDelta | None = None
    alternate_repair_delta: QueryScopeDelta | None = None
    plan: QueryPlanDraft | None = None


class GroupedSummaryDecision(_NarrowActiveDecision):
    """Aggregate and evidence refinements over a summary."""

    extraction: ActiveReasonerExtraction | None = None
    rank: QueryRankType | None = None
    target_text: str | None = None
    target_amount: float | None = None
    recipient_name: str | None = None
    referenced_frame_ids: list[str] | None = None
    coverage_intent: CoverageIntentType | None = None
    transaction_direction_delta: TransactionDirectionDeltaType | None = None
    drill_down_index: int | None = None
    drill_down_action: DrillDownActionType | None = None
    fact_field: FactFieldType | None = None


class InsightDecision(BaseModel):
    """Small adapter for grounded insight continuations.

    Insight follow-ups do not need the fresh-query extraction union: evidence
    selection and reconciliation are applied against the stored insight
    contract.  Keeping that large parser model out of this role materially
    reduces both the provider schema and prompt-input cost.
    """

    model_config = ConfigDict(extra="ignore", json_schema_extra=_strip_llm_schema_annotations)

    decision: Literal["continuation", "fresh_query", "reinterpret_query", "new_query", "end_session"]
    confidence: float | None = None
    reason: str | None = None
    continuation_type: ContinuationType | None = None
    followup_intent: FollowupIntentType | None = None
    target_text: str | None = None
    target_amount: float | None = None
    referenced_frame_ids: list[str] | None = None
    answer_mode: AnswerModeType | None = None
    coverage_intent: CoverageIntentType | None = None
    rank: QueryRankType | None = None
    drill_down_index: int | None = None
    drill_down_action: DrillDownActionType | None = None
    response_text: str | None = None
    end_session_response: str | None = None
    end_session_kind: EndSessionKindType | None = None
    preferences_update: QueryPreferenceUpdate | None = None

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = super().model_json_schema(*args, **kwargs)
        _strip_llm_schema_annotations(schema)
        return schema

    def to_public_decision(self) -> QuerySemanticDecision:
        return QuerySemanticDecision.model_validate(self.model_dump(exclude_none=True))


class HistoricalFrameDecision(_NarrowActiveDecision):
    """Comparison or selection grounded in recent immutable query frames."""

    answer_mode: AnswerModeType | None = None
    referenced_frame_ids: list[str] | None = None
    grounded_operation: GroundedOperationType | None = None
    target_index: int | None = None
    target_text: str | None = None


class PendingClarificationDecision(BaseModel):
    """Structured output for pending-clarification turns."""

    # See _NarrowActiveDecision: provider output is an untrusted adapter at
    # this boundary.  The public/persisted decision remains typed below.
    model_config = ConfigDict(extra="ignore", json_schema_extra=_strip_llm_schema_annotations)

    decision: Literal["clarification_answer", "fresh_query", "reinterpret_query", "new_query", "end_session"]
    confidence: float | None = Field(default=None)
    reason: str | None = Field(default=None)
    extraction: ReasonerQueryExtraction | None = Field(default=None)
    time_period: str | None = Field(default=None)
    clarification_patch: ClarificationPatch | None = Field(default=None)
    answer_mode: AnswerModeType | None = Field(default=None)
    referenced_frame_ids: list[str] | None = Field(default=None)
    grounded_operation: GroundedOperationType | None = Field(default=None)
    end_session_response: str | None = Field(default=None)
    end_session_kind: EndSessionKindType | None = Field(default=None)
    response_text: str | None = Field(default=None)
    contextual_hint: str | None = Field(default=None)

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = super().model_json_schema(*args, **kwargs)
        _strip_llm_schema_annotations(schema)
        return schema

    def to_public_decision(self) -> QuerySemanticDecision:
        extraction = self.extraction.to_query_extraction_result() if self.extraction is not None else None
        return QuerySemanticDecision(
            decision=self.decision,
            confidence=self.confidence,
            reason=self.reason,
            extraction=extraction,
            time_period=self.time_period,
            clarification_patch=self.clarification_patch,
            answer_mode=self.answer_mode,
            referenced_frame_ids=self.referenced_frame_ids,
            grounded_operation=self.grounded_operation,
            end_session_response=self.end_session_response,
            end_session_kind=self.end_session_kind,
            response_text=self.response_text,
            contextual_hint=self.contextual_hint,
        )


@dataclass
class SemanticReasonerContext:
    """Context passed into the semantic reasoner."""

    message: str
    today: date
    language: str
    query_request: QueryRequest | None = None
    pending_input: PendingFieldClarification | None = None
    items: list[QueryResultItem] | None = None
    surface_view: SurfaceView | None = None
    query_frames: list[QueryFrame] | None = None
    active_focus: QueryFocus | None = None
    query_preferences: dict[str, Any] | None = None
    turn_id: str | None = None
    inbound_message_id: str | None = None

    @property
    def session_mode(self) -> SemanticContextModeType:
        if self.pending_input is not None:
            return "pending_clarification"
        if self.query_request is not None:
            return "active_result"
        return "none"
