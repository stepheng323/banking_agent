"""Contracts for the query semantic reasoner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from banking.transactions.query.contracts import SurfaceView
from banking.transactions.query.models.conversation import QueryFocus, QueryScopeDelta
from banking.transactions.query.models.domain import (
    Filters,
    QueryFrame,
    QueryRequest,
    QueryResultItem,
    TimeRange,
)
from banking.transactions.query.models.extraction import (
    ClarificationPatch,
    PendingClarificationState,
    QueryExtractionResult,
    QueryPlanDraft,
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
    reason: str | None = None
    extraction: ReasonerQueryExtraction | None = None
    repair_delta: QueryScopeDelta | None = None
    alternate_repair_delta: QueryScopeDelta | None = None
    preferences_update: QueryPreferenceUpdate | None = None
    continuation_type: ContinuationType | None = None
    followup_intent: FollowupIntentType | None = None
    time_period: str | None = None
    # Some providers echo the caller's raw text at the decision level. It is
    # not a runtime decision field; accept and discard it so an otherwise
    # valid continuation does not degrade into a fresh-query fallback.
    raw_query: str | None = None
    response_text: str | None = None
    # Reconciliation is grounded in compact, retained query frames.  Keep this
    # common to every active surface so a challenge is never forced through a
    # normal item-selection path merely because its current surface is a list
    # or a direct answer.
    referenced_frame_ids: list[str] | None = None
    end_session_response: str | None = None
    end_session_kind: EndSessionKindType | None = None

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = super().model_json_schema(*args, **kwargs)
        _strip_llm_schema_annotations(schema)
        return schema

    def to_public_decision(self) -> QuerySemanticDecision:
        payload = self.model_dump(exclude_none=True)
        payload.pop("raw_query", None)
        extraction = self.extraction.to_query_extraction_result() if self.extraction is not None else None
        payload["extraction"] = extraction
        return QuerySemanticDecision.model_validate(payload)


class FocusedItemDecision(_NarrowActiveDecision):
    """Fact and drill-down decisions for one focused result."""

    drill_down_index: int | None = None
    drill_down_action: DrillDownActionType | None = None
    fact_field: FactFieldType | None = None
    requested_field: QueryTargetFieldType | None = None
    target_text: str | None = None
    target_amount: float | None = None
    answer_mode: AnswerModeType | None = None
    delta_type: DeltaType | None = None
    time_range: TimeRange | None = None
    filters: Filters | None = None


class TransactionListDecision(_NarrowActiveDecision):
    """Selection, pagination, aggregation, coverage, and refinement over a visible list."""

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
    answer_mode: AnswerModeType | None = None
    delta_type: DeltaType | None = None
    time_range: TimeRange | None = None
    filters: Filters | None = None
    recipient_name: str | None = None
    result_limit: int | None = None
    result_reference: ResultReferenceType | None = None
    plan: QueryPlanDraft | None = None


class CompositeDecision(TransactionListDecision):
    """A follow-up targeting one visible section of a multi-step answer."""

    target_step_id: str | None = None
    grounded_operation: GroundedOperationType | None = None


class GroupedSummaryDecision(_NarrowActiveDecision):
    """Aggregate and evidence refinements over a summary."""

    answer_mode: AnswerModeType | None = None
    delta_type: DeltaType | None = None
    time_range: TimeRange | None = None
    filters: Filters | None = None
    result_limit: int | None = None
    rank: QueryRankType | None = None
    target_text: str | None = None
    target_amount: float | None = None
    recipient_name: str | None = None
    coverage_intent: CoverageIntentType | None = None
    transaction_direction_delta: TransactionDirectionDeltaType | None = None
    drill_down_index: int | None = None
    drill_down_action: DrillDownActionType | None = None
    fact_field: FactFieldType | None = None
    plan: QueryPlanDraft | None = None


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
    pending_clarification: PendingClarificationState | None = None
    items: list[QueryResultItem] | None = None
    surface_view: SurfaceView | None = None
    query_frames: list[QueryFrame] | None = None
    active_focus: QueryFocus | None = None
    query_preferences: dict[str, Any] | None = None
    turn_id: str | None = None
    inbound_message_id: str | None = None

    @property
    def session_mode(self) -> SemanticContextModeType:
        if self.pending_clarification is not None:
            return "pending_clarification"
        if self.query_request is not None:
            return "active_result"
        return "none"
