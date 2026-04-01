"""Unified semantic reasoner for the query domain."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from time import perf_counter
from typing import Any, Literal, cast

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from apps.core.src.agent.graphs.query.models import (
    Filters,
    PendingClarificationState,
    QueryExecutionContract,
    QueryExtractionResult,
    QueryFrame,
    QueryOperation,
    QueryResultItem,
    ReasonerQueryExtraction,
    TimeRange,
)
from apps.core.src.agent.graphs.query.prompts.main import (
    QUERY_SEMANTIC_REASONER_CONTEXT,
    QUERY_SEMANTIC_REASONER_SYSTEM,
)
from apps.core.src.agent.graphs.query.services.continuity import ContinuationClassifier
from apps.core.src.agent.graphs.query.services.query_shortcuts import resolve_query_shortcut
from apps.core.src.agent.shared.query_contracts import SurfaceView, SurfaceViewMode
from shared.utils.logging import get_logger

logger = get_logger(__name__)
_MAX_PROMPT_ITEMS = 3
_MAX_PROMPT_QUERY_FRAMES = 3
_SURFACE_CONTEXT_KEYS = ("type", "view", "count", "total_results", "has_more", "group_by")
_ITEM_METADATA_KEYS = ("status", "bank_name", "recipient_name", "recipient_bank_name", "type", "transaction_type")


# Type Aliases for Semantic Reasoner
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
    "explain_aggregate_scope",
    "drill_down",
    "recipient_drill_down",
    "aggregate",
    "unclear",
]

FollowupIntentType = Literal["refine_existing", "replace_scope", "continue_pagination", "none"]

AnswerModeType = Literal["memory_answer", "grounded_query", "ask_clarify"]

GroundedOperationType = Literal["compare_frames", "select_frame", "show_transactions", "reuse_frame"]

DeltaType = Literal["filter", "time", "limit", "reference", "none"]

ResultReferenceType = Literal["latest", "oldest"]

DrillDownActionType = Literal["view_details", "get_receipt", "report_issue", "re_transfer", "answer_fact"]

EndSessionKindType = Literal["courtesy", "dismissive", "generic"]

FactFieldType = Literal["status", "amount", "recipient", "bank", "date"]

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


class QuerySemanticReasoner:
    """Single semantic reasoner for fresh query, clarification, and continuation."""

    def __init__(self, llm: Runnable):
        typed_llm = cast(Any, llm)
        self._active_structured_llm = typed_llm.with_structured_output(ActiveContinuationDecision)
        self._pending_structured_llm = typed_llm.with_structured_output(PendingClarificationDecision)
        self._continuation_classifier = ContinuationClassifier()

    @staticmethod
    def _log_reasoner_decision(
        *,
        context_mode: SemanticContextModeType,
        decision: QuerySemanticDecision,
        llm_used: bool,
    ) -> None:
        logger.info(
            "query_reasoner_decision",
            reasoner_decision=decision.decision,
            reasoner_context_mode=context_mode,
            reasoner_llm_used=llm_used,
            continuation_type=decision.continuation_type,
            query_operation=decision.query_operation.value if decision.query_operation is not None else None,
            confidence=decision.confidence,
            reason=decision.reason,
        )

    @staticmethod
    def _log_query_trace(
        *,
        context: SemanticReasonerContext,
        phase: str,
        latency_ms: float,
        llm_used: bool,
        decision: QuerySemanticDecision | None = None,
        prompt_item_count: int = 0,
        prompt_frame_count: int = 0,
        prompt_surface_type: str | None = None,
        outcome: str = "ok",
        reasoner_schema: str | None = None,
        context_bytes: int | None = None,
        llm_calls_used: int | None = None,
        single_llm_invariant: bool | None = None,
    ) -> None:
        logger.info(
            "query_trace",
            turn_id=context.turn_id,
            inbound_message_id=context.inbound_message_id,
            query_phase=phase,
            latency_ms=round(latency_ms, 2),
            session_mode=context.session_mode,
            llm_used=llm_used,
            semantic_decision=decision.decision if decision is not None else None,
            continuation_type=decision.continuation_type if decision is not None else None,
            query_operation=decision.query_operation.value
            if decision is not None and decision.query_operation is not None
            else None,
            prompt_item_count=prompt_item_count,
            prompt_frame_count=prompt_frame_count,
            prompt_surface_type=prompt_surface_type,
            reasoner_schema=reasoner_schema,
            context_bytes=context_bytes,
            llm_calls_used=llm_calls_used,
            single_llm_invariant=single_llm_invariant,
            outcome=outcome,
        )

    @staticmethod
    def _annotate_decision(
        *,
        context_mode: SemanticContextModeType,
        decision: QuerySemanticDecision,
        llm_used: bool,
        deterministic_surface_action: str | None = None,
        reasoner_schema: ReasonerSchemaType | None = None,
    ) -> QuerySemanticDecision:
        decision.semantic_context_mode = context_mode
        decision.semantic_llm_used = llm_used
        decision.deterministic_surface_action = deterministic_surface_action
        decision.semantic_reasoner_schema = reasoner_schema
        decision.llm_calls_used = 1 if llm_used else 0
        decision.single_llm_invariant = True
        return decision

    @staticmethod
    def _normalize(text: str) -> str:
        compact = " ".join(text.lower().strip().split())
        return compact.strip('.,!?;:"`~()[]{}')

    @staticmethod
    def _serialize(value: Any) -> str:
        if value is None:
            return "none"
        try:
            if hasattr(value, "model_dump"):
                return json.dumps(value.model_dump(mode="json"), ensure_ascii=True, default=str)
            if isinstance(value, list):
                serialized = []
                for item in value:
                    if hasattr(item, "model_dump"):
                        serialized.append(item.model_dump(mode="json"))
                    else:
                        serialized.append(item)
                return json.dumps(serialized, ensure_ascii=True, default=str)
            return json.dumps(value, ensure_ascii=True, default=str)
        except Exception:
            return str(value)

    @staticmethod
    def _serialize_surface_context(surface_view: SurfaceView | None) -> str:
        if surface_view is None or not isinstance(surface_view.context, dict):
            return "none"
        payload = {
            key: surface_view.context.get(key)
            for key in _SURFACE_CONTEXT_KEYS
            if key in surface_view.context
        }
        payload["mode"] = surface_view.mode.value
        return QuerySemanticReasoner._serialize(payload or None)

    @staticmethod
    def _surface_type_name(
        *,
        surface_view: SurfaceView | None,
    ) -> str:
        if surface_view is None:
            return "none"
        mode_map = {
            SurfaceViewMode.DIRECT_ANSWER: "single_item",
            SurfaceViewMode.TRANSACTION_LIST: "list",
            SurfaceViewMode.GROUPED_SUMMARY: "summary",
            SurfaceViewMode.CLARIFICATION: "clarification",
        }
        return mode_map.get(surface_view.mode, "none")

    @classmethod
    def _serialize_surface_snapshot(
        cls,
        *,
        surface_view: SurfaceView | None,
    ) -> str:
        return cls._serialize_surface_context(surface_view)

    @classmethod
    def _serialize_query_anchor(cls, query_contract: QueryExecutionContract | None) -> str:
        if query_contract is None:
            return "none"
        payload = {
            "intent": query_contract.intent.value,
            "query_operation": (
                query_contract.query_operation.value if query_contract.query_operation is not None else None
            ),
            "time_start": query_contract.time_start.isoformat(),
            "time_end": query_contract.time_end.isoformat(),
            "filters": query_contract.filters.model_dump(exclude_none=True) if query_contract.filters is not None else None,
            "aggregation": (
                query_contract.aggregation.model_dump(exclude_none=True) if query_contract.aggregation is not None else None
            ),
            "result_limit": query_contract.result_limit,
            "result_reference": query_contract.result_reference,
        }
        return cls._serialize(payload)

    @staticmethod
    def _serialize_items(items: list[QueryResultItem] | None) -> tuple[str, int]:
        if not items:
            return "[]", 0
        bounded_items = items[:_MAX_PROMPT_ITEMS]
        payload = [
            {
                "id": item.id,
                "description": item.description,
                "amount": item.amount,
                "date": item.date.isoformat(),
                "metadata": (
                    {key: item.metadata.get(key) for key in _ITEM_METADATA_KEYS if key in item.metadata}
                    if isinstance(item.metadata, dict)
                    else None
                ),
            }
            for item in bounded_items
        ]
        return QuerySemanticReasoner._serialize(payload), len(bounded_items)

    @staticmethod
    def _serialize_query_frames(query_frames: list[QueryFrame] | None) -> tuple[str, int]:
        if not query_frames:
            return "[]", 0

        bounded_frames = query_frames[-_MAX_PROMPT_QUERY_FRAMES:]
        payload = [
            {
                "frame_id": frame.frame_id,
                "turn_index": frame.turn_index,
                "summary_text": frame.summary_text,
                "surface_type": frame.surface_type.value if frame.surface_type else None,
                "facts": frame.facts.model_dump(exclude_none=True),
            }
            for frame in bounded_frames
        ]
        return QuerySemanticReasoner._serialize(payload), len(bounded_frames)

    @classmethod
    def _deterministic_surface_action(
        cls,
        *,
        message: str,
        language: str,
        surface_view: SurfaceView | None,
    ) -> QuerySemanticDecision | None:
        surface_mode = cls._continuation_classifier_surface_type(surface_view=surface_view)
        if surface_mode not in {SurfaceViewMode.DIRECT_ANSWER, SurfaceViewMode.TRANSACTION_LIST}:
            return None
        shortcut = resolve_query_shortcut(message, language)
        if shortcut is None or shortcut.kind not in {"actionable", "detail"}:
            return None
        action_map = {
            "show_details": ("deterministic_view_details", "view_details"),
            "get_receipt": ("deterministic_receipt", "get_receipt"),
            "report_issue": ("deterministic_report_issue", "report_issue"),
        }
        action_tuple = action_map.get(shortcut.action)
        if action_tuple is None:
            return None
        reason, drill_down_action = action_tuple
        return QuerySemanticDecision(
            decision="continuation",
            confidence=1.0,
            reason=reason,
            continuation_type="drill_down",
            drill_down_index=0,
            drill_down_action=cast(
                Literal["view_details", "get_receipt", "report_issue", "re_transfer", "answer_fact"],
                drill_down_action,
            ),
        )

    @staticmethod
    def _continuation_classifier_surface_type(
        *,
        surface_view: SurfaceView | None,
    ) -> SurfaceViewMode | None:
        return surface_view.mode if surface_view is not None else None

    def _guardrail_end_session(self, *, message: str, language: str) -> QuerySemanticDecision | None:
        guarded = self._continuation_classifier._guardrail_classify(
            message=message,
            items=None,
            surface_view=None,
            language=language,
        )
        if guarded is None or guarded[0] != "end_session":
            return None
        data = guarded[1]
        return QuerySemanticDecision(
            decision="end_session",
            confidence=data.get("confidence"),
            reason=data.get("reason"),
            end_session_response=data.get("end_session_response"),
        )

    async def _invoke_llm(
        self,
        context: SemanticReasonerContext,
    ) -> QuerySemanticDecision:
        items_section, prompt_item_count = self._serialize_items(context.items)
        query_frames_section, prompt_frame_count = self._serialize_query_frames(context.query_frames)
        prompt_surface_type = self._surface_type_name(surface_view=context.surface_view)
        dynamic_context = QUERY_SEMANTIC_REASONER_CONTEXT.format(
            today=context.today.isoformat(),
            language=context.language,
            session_mode=context.session_mode,
            message=context.message,
            current_query=self._serialize_query_anchor(context.query_contract),
            pending_clarification=self._serialize(context.pending_clarification),
            surface_type=prompt_surface_type,
            surface_context=self._serialize_surface_snapshot(surface_view=context.surface_view),
            items_section=items_section,
            query_frames_section=query_frames_section,
        )
        messages = [
            SystemMessage(content=QUERY_SEMANTIC_REASONER_SYSTEM),
            HumanMessage(content=dynamic_context),
        ]
        prompt_context_bytes = len(dynamic_context.encode("utf-8"))
        if context.session_mode == "pending_clarification":
            structured_llm = self._pending_structured_llm
            reasoner_schema = "pending_clarification"
        else:
            structured_llm = self._active_structured_llm
            reasoner_schema = "active_continuation"
        started_at = perf_counter()
        try:
            raw_decision = await structured_llm.ainvoke(messages)
        except Exception:
            self._log_query_trace(
                context=context,
                phase="semantic_reasoner",
                latency_ms=(perf_counter() - started_at) * 1000.0,
                llm_used=True,
                decision=None,
                prompt_item_count=prompt_item_count,
                prompt_frame_count=prompt_frame_count,
                prompt_surface_type=prompt_surface_type,
                outcome="failed",
                reasoner_schema=reasoner_schema,
                context_bytes=prompt_context_bytes,
                llm_calls_used=1,
                single_llm_invariant=True,
            )
            raise
        decision = raw_decision.to_public_decision() if hasattr(raw_decision, "to_public_decision") else raw_decision
        self._log_query_trace(
            context=context,
            phase="semantic_reasoner",
            latency_ms=(perf_counter() - started_at) * 1000.0,
            llm_used=True,
            decision=decision,
            prompt_item_count=prompt_item_count,
            prompt_frame_count=prompt_frame_count,
            prompt_surface_type=prompt_surface_type,
            reasoner_schema=reasoner_schema,
            context_bytes=prompt_context_bytes,
            llm_calls_used=1,
            single_llm_invariant=True,
        )
        return decision

    async def _return_annotated_decision(
        self,
        *,
        context: SemanticReasonerContext,
        decision: QuerySemanticDecision,
        llm_used: bool,
        deterministic_surface_action: str | None = None,
        latency_ms: float = 0.0,
        phase: str = "semantic_reasoner",
        reasoner_schema: Literal["active_continuation", "pending_clarification"] | None = None,
    ) -> QuerySemanticDecision:
        annotated = self._annotate_decision(
            context_mode=context.session_mode,
            decision=decision,
            llm_used=llm_used,
            deterministic_surface_action=deterministic_surface_action,
            reasoner_schema=reasoner_schema,
        )
        self._log_reasoner_decision(context_mode=context.session_mode, decision=annotated, llm_used=llm_used)
        if not llm_used:
            self._log_query_trace(
                context=context,
                phase=phase,
                latency_ms=latency_ms,
                llm_used=False,
                decision=annotated,
                prompt_surface_type=self._surface_type_name(surface_view=context.surface_view),
                reasoner_schema=reasoner_schema,
                context_bytes=0,
                llm_calls_used=0,
                single_llm_invariant=True,
            )
        return annotated

    async def reason(self, context: SemanticReasonerContext) -> QuerySemanticDecision:
        if context.session_mode == "none":
            return await self._return_annotated_decision(
                context=context,
                decision=QuerySemanticDecision(
                    decision="fresh_query",
                    confidence=1.0,
                    reason="no_active_session_fast_path",
                ),
                llm_used=False,
                latency_ms=0.0,
                phase="fresh_query_fast_path",
            )

        if context.session_mode == "pending_clarification":
            started_at = perf_counter()
            guardrail_end = self._guardrail_end_session(message=context.message, language=context.language)
            if guardrail_end is not None:
                return await self._return_annotated_decision(
                    context=context,
                    decision=guardrail_end,
                llm_used=False,
                latency_ms=(perf_counter() - started_at) * 1000.0,
                phase="pending_clarification_guardrail",
                reasoner_schema="pending_clarification",
            )
        elif context.session_mode == "active_result":
            started_at = perf_counter()
            deterministic_surface = self._deterministic_surface_action(
                message=context.message,
                language=context.language,
                surface_view=context.surface_view,
            )
            if deterministic_surface is not None:
                deterministic_surface = await self._return_annotated_decision(
                    context=context,
                    decision=deterministic_surface,
                    llm_used=False,
                    deterministic_surface_action=deterministic_surface.drill_down_action,
                    latency_ms=(perf_counter() - started_at) * 1000.0,
                    phase="active_result_shortcut",
                    reasoner_schema="active_continuation",
                )
                logger.info(
                    "query_surface_action_deterministic",
                    reasoner_context_mode=context.session_mode,
                    action=deterministic_surface.drill_down_action,
                    reason=deterministic_surface.reason,
                )
                return deterministic_surface

            guarded = self._continuation_classifier._guardrail_classify(
                message=context.message,
                items=context.items,
                surface_view=context.surface_view,
                language=context.language,
            )
            if guarded is not None:
                continuation_type, data = guarded
                if continuation_type == "end_session":
                    guardrail_decision = QuerySemanticDecision(
                        decision="end_session",
                        confidence=data.get("confidence"),
                        reason=data.get("reason"),
                        end_session_response=data.get("end_session_response"),
                    )
                elif continuation_type == "recipient_drill_down":
                    guardrail_decision = QuerySemanticDecision(
                        decision="continuation",
                        confidence=data.get("confidence"),
                        reason=data.get("reason"),
                        continuation_type="recipient_drill_down",
                        followup_intent="none",
                        delta_type=cast(Any, data.get("delta_type")),
                        recipient_name=data.get("recipient_name"),
                        fact_field=cast(Any, data.get("fact_field")),
                    )
                elif continuation_type == "time_delta":
                    guardrail_decision = QuerySemanticDecision(
                        decision="continuation",
                        confidence=data.get("confidence"),
                        reason=data.get("reason"),
                        continuation_type="time_delta",
                        followup_intent="replace_scope",
                        time_period=data.get("time_period"),
                    )
                elif continuation_type == "aggregate":
                    guardrail_decision = QuerySemanticDecision(
                        decision="continuation",
                        confidence=data.get("confidence"),
                        reason=data.get("reason"),
                        continuation_type="aggregate",
                        followup_intent="refine_existing",
                    )
                elif continuation_type == "grouped_total_followup":
                    guardrail_decision = QuerySemanticDecision(
                        decision="continuation",
                        confidence=data.get("confidence"),
                        reason=data.get("reason"),
                        continuation_type="grouped_total_followup",
                        followup_intent="refine_existing",
                    )
                elif continuation_type == "filter_delta":
                    guardrail_decision = QuerySemanticDecision(
                        decision="continuation",
                        confidence=data.get("confidence"),
                        reason=data.get("reason"),
                        continuation_type="filter_delta",
                        followup_intent="refine_existing",
                        delta_type=cast(Any, "filter"),
                    )
                elif continuation_type == "fresh_query_reset":
                    guardrail_decision = QuerySemanticDecision(
                        decision="new_query",
                        confidence=data.get("confidence"),
                        reason=data.get("reason"),
                    )
                else:
                    guardrail_decision = QuerySemanticDecision(
                        decision="continuation",
                        confidence=data.get("confidence"),
                        reason=data.get("reason"),
                        continuation_type="drill_down",
                        followup_intent="none",
                        drill_down_index=data.get("drill_down_index"),
                        drill_down_action=data.get("drill_down_action"),
                    )
                return await self._return_annotated_decision(
                    context=context,
                    decision=guardrail_decision,
                    llm_used=False,
                    latency_ms=(perf_counter() - started_at) * 1000.0,
                    phase="active_result_guardrail",
                    reasoner_schema="active_continuation",
                )

        try:
            decision = await self._invoke_llm(context)
            if decision.extraction is not None:
                decision.extraction.raw_query = decision.extraction.raw_query or context.message
                if decision.extraction.query_operation is None and decision.query_operation is not None:
                    decision.extraction.query_operation = decision.query_operation
            if decision.query_operation is None and decision.extraction is not None:
                decision.query_operation = decision.extraction.query_operation
            decision = await self._return_annotated_decision(
                context=context,
                decision=decision,
                llm_used=True,
                reasoner_schema="pending_clarification"
                if context.session_mode == "pending_clarification"
                else "active_continuation",
            )
            return decision
        except Exception as exc:
            logger.error("query_semantic_reasoner_failed", error=str(exc))
            if context.session_mode == "none":
                return await self._return_annotated_decision(
                    context=context,
                    decision=QuerySemanticDecision(
                        decision="fresh_query",
                        confidence=0.0,
                        reason="fallback_empty_fresh_query",
                        extraction=QueryExtractionResult(raw_query=context.message),
                    ),
                    llm_used=False,
                    reasoner_schema="pending_clarification"
                    if context.session_mode == "pending_clarification"
                    else "active_continuation",
                )
            return await self._return_annotated_decision(
                context=context,
                decision=QuerySemanticDecision(
                    decision="new_query",
                    confidence=0.0,
                    reason="fallback_new_query",
                    extraction=QueryExtractionResult(raw_query=context.message),
                ),
                llm_used=False,
                reasoner_schema="pending_clarification"
                if context.session_mode == "pending_clarification"
                else "active_continuation",
            )
