"""Unified semantic reasoner for the query domain."""

from __future__ import annotations

import json
import re
from time import perf_counter
from typing import Any, Literal, cast

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from banking.transactions.query.continuations.classifier import ContinuationClassifier
from banking.transactions.query.contracts import SurfaceView, SurfaceViewMode
from banking.transactions.query.models.domain import (
    QueryExecutionContract,
    QueryFrame,
    QueryResultItem,
)
from banking.transactions.query.models.extraction import QueryExtractionResult
from banking.transactions.query.prompts.main import (
    QUERY_SEMANTIC_REASONER_CONTEXT,
    QUERY_SEMANTIC_REASONER_SYSTEM,
)
from banking.transactions.query.services.reasoning import models as reasoner_models
from banking.transactions.query.services.reasoning.shortcuts import resolve_query_shortcut
from shared.observability.llm import ainvoke_with_config, build_llm_runnable_config
from shared.observability.llm_call_metrics import record_llm_call, structured_output_metrics
from shared.utils.logging import get_logger

logger = get_logger(__name__)
_MAX_PROMPT_ITEMS = 5
_MAX_PROMPT_QUERY_FRAMES = 3
_SURFACE_CONTEXT_KEYS = (
    "type",
    "view",
    "count",
    "total_results",
    "has_more",
    "group_by",
    "focus_type",
    "selected_item_id",
    "ranked_type",
)
_ITEM_METADATA_KEYS = ("status", "bank_name", "recipient_name", "recipient_bank_name", "type", "transaction_type")
_ORDINAL_WORDS: dict[str, int] = {
    "first": 0,
    "1st": 0,
    "second": 1,
    "2nd": 1,
    "third": 2,
    "3rd": 2,
    "fourth": 3,
    "4th": 3,
    "fifth": 4,
    "5th": 4,
}

class QuerySemanticReasoner:
    """Single semantic reasoner for fresh query, clarification, and continuation."""

    def __init__(self, llm: Runnable):
        self._base_llm = llm
        typed_llm = cast(Any, llm)
        self._active_structured_llm = typed_llm.with_structured_output(reasoner_models.ActiveContinuationDecision)
        self._pending_structured_llm = typed_llm.with_structured_output(reasoner_models.PendingClarificationDecision)
        self._continuation_classifier = ContinuationClassifier()

    @staticmethod
    def _log_reasoner_decision(
        *,
        context_mode: reasoner_models.SemanticContextModeType,
        decision: reasoner_models.QuerySemanticDecision,
        llm_used: bool,
    ) -> None:
        logger.info(
            "query_reasoner_decision",
            reasoner_decision=decision.decision,
            reasoner_context_mode=context_mode,
            reasoner_llm_used=llm_used,
            continuation_type=decision.continuation_type,
            confidence=decision.confidence,
            reason=decision.reason,
        )

    @staticmethod
    def _log_query_trace(
        *,
        context: reasoner_models.SemanticReasonerContext,
        phase: str,
        latency_ms: float,
        llm_used: bool,
        decision: reasoner_models.QuerySemanticDecision | None = None,
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
    def _log_llm_call(
        *,
        duration_ms: float,
        reasoner_schema: reasoner_models.ReasonerSchemaType,
        prompt_item_count: int,
        prompt_frame_count: int,
        prompt_surface_type: str | None,
        context_bytes: int,
    ) -> None:
        logger.info(
            "query_reasoner_llm_call",
            duration_ms=round(duration_ms, 2),
            reasoner_schema=reasoner_schema,
            prompt_item_count=prompt_item_count,
            prompt_frame_count=prompt_frame_count,
            prompt_surface_type=prompt_surface_type,
            context_bytes=context_bytes,
        )

    def _record_llm_call(
        self,
        *,
        duration_ms: float,
        reasoner_schema: reasoner_models.ReasonerSchemaType,
        prompt_item_count: int,
        prompt_frame_count: int,
        prompt_surface_type: str | None,
        dynamic_context: str,
        output: Any | None = None,
        error_type: str | None = None,
    ) -> None:
        output_metrics = structured_output_metrics(output) if output is not None else {}
        model = getattr(self._base_llm, "model_name", None) or getattr(self._base_llm, "model", None)
        record_llm_call(
            event_name="query_reasoner_llm_call",
            duration_ms=duration_ms,
            model=model,
            response_type=type(output).__name__ if output is not None else None,
            system_chars=len(QUERY_SEMANTIC_REASONER_SYSTEM),
            user_chars=len(dynamic_context),
            output_json_chars=output_metrics.get("output_json_chars"),
            output_token_estimate=output_metrics.get("output_token_estimate"),
            error_type=error_type,
            extra_fields={
                **output_metrics,
                "reasoner_schema": reasoner_schema,
                "prompt_item_count": prompt_item_count,
                "prompt_frame_count": prompt_frame_count,
                "prompt_surface_type": prompt_surface_type,
                "context_bytes": len(dynamic_context.encode("utf-8")),
            },
        )

    @staticmethod
    def _annotate_decision(
        *,
        context_mode: reasoner_models.SemanticContextModeType,
        decision: reasoner_models.QuerySemanticDecision,
        llm_used: bool,
        deterministic_surface_action: str | None = None,
        reasoner_schema: reasoner_models.ReasonerSchemaType | None = None,
    ) -> reasoner_models.QuerySemanticDecision:
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
        payload = {key: surface_view.context.get(key) for key in _SURFACE_CONTEXT_KEYS if key in surface_view.context}
        payload["mode"] = surface_view.mode.value
        if surface_view.lead_text:
            payload["lead_text"] = surface_view.lead_text
        if len(surface_view.items) == 1:
            item = surface_view.items[0]
            item_metadata = item.metadata if isinstance(item.metadata, dict) else {}
            payload["focused_item"] = {
                "id": item.id,
                "label": item.label,
                "amount": item.amount,
                "count": item.count,
                "selection_kind": item.payload.selection_kind,
                "entity_type": item.payload.entity_type,
                "fact_capabilities": item.payload.fact_capabilities,
                "metadata": {
                    key: item_metadata.get(key)
                    for key in (
                        "date",
                        "status",
                        "bank_name",
                        "recipient_name",
                        "recipient_bank_name",
                        "counterparty",
                        "type",
                        "transaction_type",
                    )
                    if key in item_metadata
                },
                "has_filters_patch": bool(item.payload.filters_patch),
                "has_time_patch": item.payload.time_patch is not None,
                "group_by": item.payload.group_by,
                "group_key": item.payload.group_key,
            }
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
            "time_start": query_contract.time_start.isoformat(),
            "time_end": query_contract.time_end.isoformat(),
            "filters": query_contract.filters.model_dump(exclude_none=True)
            if query_contract.filters is not None
            else None,
            "aggregation": (
                query_contract.aggregation.model_dump(exclude_none=True)
                if query_contract.aggregation is not None
                else None
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
                "visible_items": frame.visible_items[:5],
            }
            for frame in bounded_frames
        ]
        return QuerySemanticReasoner._serialize(payload), len(bounded_frames)

    @staticmethod
    def _deterministic_ordinal_index(normalized: str) -> int | None:
        for token, index in _ORDINAL_WORDS.items():
            if re.search(rf"\b{re.escape(token)}\b", normalized):
                return index
        match = re.search(r"\b(?:item|number|no\.?|#)\s*([1-5])\b", normalized)
        if match:
            return int(match.group(1)) - 1
        return None

    @staticmethod
    def _has_visible_items(surface_view: SurfaceView | None) -> bool:
        return surface_view is not None and bool(surface_view.items)

    @classmethod
    def _deterministic_visible_followup(
        cls,
        *,
        message: str,
        surface_view: SurfaceView | None,
    ) -> reasoner_models.QuerySemanticDecision | None:
        surface_mode = cls._continuation_classifier_surface_type(surface_view=surface_view)
        if surface_mode not in {SurfaceViewMode.DIRECT_ANSWER, SurfaceViewMode.TRANSACTION_LIST}:
            return None
        if not cls._has_visible_items(surface_view):
            return None

        normalized = cls._normalize(message)
        ordinal_index = cls._deterministic_ordinal_index(normalized)
        if ordinal_index is None:
            return None
        if not re.search(r"\b(?:show|open|view|see|details?|transaction|payment|transfer|one)\b", normalized):
            return None
        return reasoner_models.QuerySemanticDecision(
            decision="continuation",
            confidence=1.0,
            reason="deterministic_visible_item_detail",
            continuation_type="drill_down",
            drill_down_index=ordinal_index,
            drill_down_action="view_details",
        )

    @classmethod
    def _deterministic_surface_action(
        cls,
        *,
        message: str,
        language: str,
        surface_view: SurfaceView | None,
    ) -> reasoner_models.QuerySemanticDecision | None:
        surface_mode = cls._continuation_classifier_surface_type(surface_view=surface_view)
        if surface_mode not in {SurfaceViewMode.DIRECT_ANSWER, SurfaceViewMode.TRANSACTION_LIST}:
            return None
        visible_followup = cls._deterministic_visible_followup(message=message, surface_view=surface_view)
        if visible_followup is not None:
            return visible_followup
        shortcut = resolve_query_shortcut(message, language)
        if shortcut is None or shortcut.kind not in {"actionable", "pagination"}:
            return None
        if shortcut.kind == "pagination":
            followup_intent: reasoner_models.FollowupIntentType = (
                "previous_pagination" if shortcut.action == "show_previous" else "continue_pagination"
            )
            return reasoner_models.QuerySemanticDecision(
                decision="continuation",
                confidence=1.0,
                reason=f"deterministic_{shortcut.action}",
                continuation_type="show_more",
                followup_intent=followup_intent,
            )
        action_map = {
            "get_receipt": ("deterministic_receipt", "get_receipt", None),
            "report_issue": ("deterministic_report_issue", "report_issue", None),
            "answer_date": ("deterministic_fact_field", "answer_fact", "date"),
        }
        action_tuple = action_map.get(shortcut.action)
        if action_tuple is None:
            return None
        reason, drill_down_action, fact_field = action_tuple
        return reasoner_models.QuerySemanticDecision(
            decision="continuation",
            confidence=1.0,
            reason=reason,
            continuation_type="drill_down",
            drill_down_index=0,
            drill_down_action=cast(
                Literal["view_details", "get_receipt", "report_issue", "re_transfer", "answer_fact"],
                drill_down_action,
            ),
            fact_field=cast(reasoner_models.FactFieldType, fact_field) if fact_field else None,
        )

    @staticmethod
    def _continuation_classifier_surface_type(
        *,
        surface_view: SurfaceView | None,
    ) -> SurfaceViewMode | None:
        return surface_view.mode if surface_view is not None else None

    def _guardrail_end_session(self, *, message: str, language: str) -> reasoner_models.QuerySemanticDecision | None:
        guarded = self._continuation_classifier._guardrail_classify(
            message=message,
            language=language,
        )
        if guarded is None or guarded[0] != "end_session":
            return None
        data = guarded[1]
        return reasoner_models.QuerySemanticDecision(
            decision="end_session",
            confidence=data.get("confidence"),
            reason=data.get("reason"),
            end_session_response=data.get("end_session_response"),
        )

    async def _invoke_llm(
        self,
        context: reasoner_models.SemanticReasonerContext,
    ) -> reasoner_models.QuerySemanticDecision:
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
            reasoner_schema: reasoner_models.ReasonerSchemaType = "pending_clarification"
        else:
            structured_llm = self._active_structured_llm
            reasoner_schema = "active_continuation"
        started_at = perf_counter()
        try:
            raw_decision = await ainvoke_with_config(
                structured_llm,
                messages,
                config=build_llm_runnable_config(
                    role="query_reasoner",
                    message_id=context.inbound_message_id,
                    turn_id=context.turn_id,
                    task_domain="query",
                    extra_metadata={
                        "session_mode": context.session_mode,
                        "reasoner_schema": reasoner_schema,
                        "prompt_item_count": prompt_item_count,
                        "prompt_frame_count": prompt_frame_count,
                    },
                )
                or None,
            )
        except Exception:
            duration_ms = (perf_counter() - started_at) * 1000.0
            self._log_llm_call(
                duration_ms=duration_ms,
                reasoner_schema=reasoner_schema,
                prompt_item_count=prompt_item_count,
                prompt_frame_count=prompt_frame_count,
                prompt_surface_type=prompt_surface_type,
                context_bytes=prompt_context_bytes,
            )
            self._record_llm_call(
                duration_ms=duration_ms,
                reasoner_schema=reasoner_schema,
                prompt_item_count=prompt_item_count,
                prompt_frame_count=prompt_frame_count,
                prompt_surface_type=prompt_surface_type,
                dynamic_context=dynamic_context,
                error_type="invoke_error",
            )
            self._log_query_trace(
                context=context,
                phase="semantic_reasoner",
                latency_ms=duration_ms,
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
        duration_ms = (perf_counter() - started_at) * 1000.0
        self._log_llm_call(
            duration_ms=duration_ms,
            reasoner_schema=reasoner_schema,
            prompt_item_count=prompt_item_count,
            prompt_frame_count=prompt_frame_count,
            prompt_surface_type=prompt_surface_type,
            context_bytes=prompt_context_bytes,
        )
        self._record_llm_call(
            duration_ms=duration_ms,
            reasoner_schema=reasoner_schema,
            prompt_item_count=prompt_item_count,
            prompt_frame_count=prompt_frame_count,
            prompt_surface_type=prompt_surface_type,
            dynamic_context=dynamic_context,
            output=raw_decision,
        )
        decision = raw_decision.to_public_decision() if hasattr(raw_decision, "to_public_decision") else raw_decision
        self._log_query_trace(
            context=context,
            phase="semantic_reasoner",
            latency_ms=duration_ms,
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
        context: reasoner_models.SemanticReasonerContext,
        decision: reasoner_models.QuerySemanticDecision,
        llm_used: bool,
        deterministic_surface_action: str | None = None,
        latency_ms: float = 0.0,
        phase: str = "semantic_reasoner",
        reasoner_schema: Literal["active_continuation", "pending_clarification"] | None = None,
    ) -> reasoner_models.QuerySemanticDecision:
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

    async def reason(self, context: reasoner_models.SemanticReasonerContext) -> reasoner_models.QuerySemanticDecision:
        if context.session_mode == "none":
            return await self._return_annotated_decision(
                context=context,
                decision=reasoner_models.QuerySemanticDecision(
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
            guardrail_end = self._guardrail_end_session(message=context.message, language=context.language)
            if guardrail_end is not None:
                return await self._return_annotated_decision(
                    context=context,
                    decision=guardrail_end,
                    llm_used=False,
                    latency_ms=(perf_counter() - started_at) * 1000.0,
                    phase="active_result_session_end_guardrail",
                    reasoner_schema="active_continuation",
                )

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

        try:
            decision = await self._invoke_llm(context)
            if decision.extraction is not None:
                decision.extraction.raw_query = decision.extraction.raw_query or context.message
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
                    decision=reasoner_models.QuerySemanticDecision(
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
                decision=reasoner_models.QuerySemanticDecision(
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
