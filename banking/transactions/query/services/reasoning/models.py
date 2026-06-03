"""Contracts for the query semantic reasoner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from apps.chat.src.agent.shared.query_contracts import SurfaceView
from banking.transactions.query.models.domain import (
    Filters,
    QueryExecutionContract,
    QueryFrame,
    QueryOperation,
    QueryResultItem,
    TimeRange,
)
from banking.transactions.query.models.extraction import (
    PendingClarificationState,
    QueryExtractionResult,
    ReasonerQueryExtraction,
)

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

SemanticContextModeType = Literal["none", "pending_clarification", "active_result"]

ReasonerSchemaType = Literal["active_continuation", "pending_clarification"]


class QuerySemanticDecision(BaseModel):
    """Unified semantic reasoner output for query turns."""

    decision: DecisionType
    confidence: float | None = Field(default=None)
    reason: str | None = Field(default=None)

    extraction: QueryExtractionResult | None = Field(default=None)
    query_operation: QueryOperation | None = Field(default=None)
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
    query_operation: QueryOperation | None = Field(default=None)
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
            query_operation=self.query_operation,
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
            drill_down_index=self.drill_down_index,
            drill_down_action=self.drill_down_action,
            recipient_name=self.recipient_name,
            end_session_response=self.end_session_response,
            end_session_kind=self.end_session_kind,
            fact_field=self.fact_field,
            response_text=self.response_text,
            contextual_hint=self.contextual_hint,
        )


class PendingClarificationDecision(BaseModel):
    """Structured output for pending-clarification turns."""

    decision: Literal["clarification_answer", "fresh_query", "reinterpret_query", "new_query", "end_session"]
    confidence: float | None = Field(default=None)
    reason: str | None = Field(default=None)
    extraction: ReasonerQueryExtraction | None = Field(default=None)
    query_operation: QueryOperation | None = Field(default=None)
    time_period: str | None = Field(default=None)
    answer_mode: AnswerModeType | None = Field(default=None)
    referenced_frame_ids: list[str] | None = Field(default=None)
    grounded_operation: GroundedOperationType | None = Field(default=None)
    end_session_response: str | None = Field(default=None)
    end_session_kind: EndSessionKindType | None = Field(default=None)
    response_text: str | None = Field(default=None)
    contextual_hint: str | None = Field(default=None)

    def to_public_decision(self) -> QuerySemanticDecision:
        extraction = self.extraction.to_query_extraction_result() if self.extraction is not None else None
        return QuerySemanticDecision(
            decision=self.decision,
            confidence=self.confidence,
            reason=self.reason,
            extraction=extraction,
            query_operation=self.query_operation,
            time_period=self.time_period,
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
    query_contract: QueryExecutionContract | None = None
    pending_clarification: PendingClarificationState | None = None
    items: list[QueryResultItem] | None = None
    surface_view: SurfaceView | None = None
    query_frames: list[QueryFrame] | None = None
    turn_id: str | None = None
    inbound_message_id: str | None = None

    @property
    def session_mode(self) -> SemanticContextModeType:
        if self.pending_clarification is not None:
            return "pending_clarification"
        if self.query_contract is not None:
            return "active_result"
        return "none"
