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
    ResultSurface,
    SurfaceType,
    TimeRange,
)
from apps.core.src.agent.graphs.query.prompts.main import (
    QUERY_SEMANTIC_REASONER_CONTEXT,
    QUERY_SEMANTIC_REASONER_SYSTEM,
)
from apps.core.src.agent.graphs.query.services.continuity import ContinuationClassifier
from apps.core.src.agent.graphs.query.services.query_shortcuts import resolve_query_shortcut
from shared.utils.logging import get_logger

logger = get_logger(__name__)
_MAX_PROMPT_ITEMS = 3
_MAX_PROMPT_QUERY_FRAMES = 3
_SURFACE_CONTEXT_KEYS = ("type", "view", "count", "total_results", "has_more", "group_by")
_ITEM_METADATA_KEYS = ("status", "bank_name", "recipient_name", "recipient_bank_name", "type", "transaction_type")


class QuerySemanticDecision(BaseModel):
    """Unified semantic reasoner output for query turns."""

    decision: Literal[
        "fresh_query",
        "clarification_answer",
        "reinterpret_query",
        "continuation",
        "new_query",
        "end_session",
    ]
    confidence: float | None = Field(default=None)
    reason: str | None = Field(default=None)

    extraction: QueryExtractionResult | None = Field(default=None)
    query_operation: QueryOperation | None = Field(default=None)
    time_period: str | None = Field(default=None)

    continuation_type: (
        Literal[
            "show_more",
            "time_delta",
            "filter_delta",
            "expand",
            "conversational",
            "drill_down",
            "recipient_drill_down",
            "aggregate",
            "unclear",
        ]
        | None
    ) = Field(default=None)
    followup_intent: Literal["refine_existing", "replace_scope", "continue_pagination", "none"] | None = Field(
        default=None
    )
    answer_mode: Literal["memory_answer", "grounded_query", "ask_clarify"] | None = Field(default=None)
    referenced_frame_ids: list[str] | None = Field(default=None)
    grounded_operation: Literal["compare_frames", "select_frame", "show_transactions", "reuse_frame"] | None = Field(
        default=None
    )
    delta_type: Literal["filter", "time", "limit", "reference", "none"] | None = Field(default=None)
    time_range: TimeRange | None = Field(default=None)
    filters: Filters | None = Field(default=None)
    result_limit: int | None = Field(default=None)
    result_reference: Literal["latest", "oldest"] | None = Field(default=None)
    drill_down_index: int | None = Field(default=None)
    drill_down_action: Literal["view_details", "get_receipt", "report_issue", "re_transfer", "answer_fact"] | None = (
        Field(default=None)
    )
    recipient_name: str | None = Field(default=None)
    end_session_response: str | None = Field(default=None)
    end_session_kind: Literal["courtesy", "dismissive", "generic"] | None = Field(default=None)
    fact_field: Literal["status", "amount", "recipient", "bank", "date"] | None = Field(default=None)
    response_text: str | None = Field(default=None)
    contextual_hint: str | None = Field(default=None)
    semantic_context_mode: Literal["none", "pending_clarification", "active_result"] | None = Field(default=None)
    semantic_llm_used: bool | None = Field(default=None)
    deterministic_surface_action: str | None = Field(default=None)


@dataclass
class SemanticReasonerContext:
    """Context passed into the semantic reasoner."""

    message: str
    today: date
    language: str
    query_contract: QueryExecutionContract | None = None
    pending_clarification: PendingClarificationState | None = None
    items: list[QueryResultItem] | None = None
    surface: ResultSurface | None = None
    query_frames: list[QueryFrame] | None = None
    turn_id: str | None = None
    inbound_message_id: str | None = None

    @property
    def session_mode(self) -> Literal["none", "pending_clarification", "active_result"]:
        if self.pending_clarification is not None:
            return "pending_clarification"
        if self.query_contract is not None:
            return "active_result"
        return "none"


class QuerySemanticReasoner:
    """Single semantic reasoner for fresh query, clarification, and continuation."""

    def __init__(self, llm: Runnable):
        self.structured_llm = cast(Any, llm).with_structured_output(QuerySemanticDecision)
        self._continuation_classifier = ContinuationClassifier()

    @staticmethod
    def _log_reasoner_decision(
        *,
        context_mode: Literal["none", "pending_clarification", "active_result"],
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
            outcome=outcome,
        )

    @staticmethod
    def _annotate_decision(
        *,
        context_mode: Literal["none", "pending_clarification", "active_result"],
        decision: QuerySemanticDecision,
        llm_used: bool,
        deterministic_surface_action: str | None = None,
    ) -> QuerySemanticDecision:
        decision.semantic_context_mode = context_mode
        decision.semantic_llm_used = llm_used
        decision.deterministic_surface_action = deterministic_surface_action
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
    def _serialize_surface_context(surface: ResultSurface | None) -> str:
        if surface is None or not isinstance(surface.context, dict):
            return "none"
        payload = {key: surface.context.get(key) for key in _SURFACE_CONTEXT_KEYS if key in surface.context}
        return QuerySemanticReasoner._serialize(payload or None)

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
                "query_contract": {
                    "intent": frame.query_contract.intent.value,
                    "time_start": frame.query_contract.time_start.isoformat(),
                    "time_end": frame.query_contract.time_end.isoformat(),
                    "filters": (
                        frame.query_contract.filters.model_dump(exclude_none=True)
                        if frame.query_contract.filters
                        else None
                    ),
                    "aggregation": (
                        frame.query_contract.aggregation.model_dump(exclude_none=True)
                        if frame.query_contract.aggregation
                        else None
                    ),
                    "comparison": (
                        frame.query_contract.comparison.model_dump(exclude_none=True)
                        if frame.query_contract.comparison
                        else None
                    ),
                },
                "surface_type": frame.surface_type.value if frame.surface_type else None,
                "surface_context": frame.surface_context,
                "facts": frame.facts.model_dump(exclude_none=True),
                "interpretation": frame.interpretation,
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
        surface: ResultSurface | None,
    ) -> QuerySemanticDecision | None:
        if surface is None or surface.type not in {SurfaceType.SINGLE_ITEM, SurfaceType.LIST}:
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

    def _guardrail_end_session(self, *, message: str, language: str) -> QuerySemanticDecision | None:
        guarded = self._continuation_classifier._guardrail_classify(
            message=message,
            items=None,
            surface=None,
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

    async def _invoke_llm(self, context: SemanticReasonerContext) -> QuerySemanticDecision:
        items_section, prompt_item_count = self._serialize_items(context.items)
        query_frames_section, prompt_frame_count = self._serialize_query_frames(context.query_frames)
        prompt_surface_type = context.surface.type.value if context.surface is not None else "none"
        dynamic_context = QUERY_SEMANTIC_REASONER_CONTEXT.format(
            today=context.today.isoformat(),
            language=context.language,
            session_mode=context.session_mode,
            message=context.message,
            current_query=self._serialize(
                context.query_contract.normalized_query if context.query_contract is not None else None
            ),
            pending_clarification=self._serialize(context.pending_clarification),
            surface_type=prompt_surface_type,
            surface_context=self._serialize_surface_context(context.surface),
            items_section=items_section,
            query_frames_section=query_frames_section,
        )
        messages = [
            SystemMessage(content=QUERY_SEMANTIC_REASONER_SYSTEM),
            HumanMessage(content=dynamic_context),
        ]
        started_at = perf_counter()
        try:
            decision = await self.structured_llm.ainvoke(messages)
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
            )
            raise
        self._log_query_trace(
            context=context,
            phase="semantic_reasoner",
            latency_ms=(perf_counter() - started_at) * 1000.0,
            llm_used=True,
            decision=decision,
            prompt_item_count=prompt_item_count,
            prompt_frame_count=prompt_frame_count,
            prompt_surface_type=prompt_surface_type,
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
    ) -> QuerySemanticDecision:
        annotated = self._annotate_decision(
            context_mode=context.session_mode,
            decision=decision,
            llm_used=llm_used,
            deterministic_surface_action=deterministic_surface_action,
        )
        self._log_reasoner_decision(context_mode=context.session_mode, decision=annotated, llm_used=llm_used)
        if not llm_used:
            self._log_query_trace(
                context=context,
                phase=phase,
                latency_ms=latency_ms,
                llm_used=False,
                decision=annotated,
                prompt_surface_type=context.surface.type.value if context.surface is not None else "none",
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
                )
        elif context.session_mode == "active_result":
            started_at = perf_counter()
            deterministic_surface = self._deterministic_surface_action(
                message=context.message,
                language=context.language,
                surface=context.surface,
            )
            if deterministic_surface is not None:
                deterministic_surface = await self._return_annotated_decision(
                    context=context,
                    decision=deterministic_surface,
                    llm_used=False,
                    deterministic_surface_action=deterministic_surface.drill_down_action,
                    latency_ms=(perf_counter() - started_at) * 1000.0,
                    phase="active_result_shortcut",
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
                surface=context.surface,
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
                elif continuation_type == "filter_delta":
                    guardrail_decision = QuerySemanticDecision(
                        decision="continuation",
                        confidence=data.get("confidence"),
                        reason=data.get("reason"),
                        continuation_type="filter_delta",
                        followup_intent="refine_existing",
                        delta_type=cast(Any, "filter"),
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
            )
