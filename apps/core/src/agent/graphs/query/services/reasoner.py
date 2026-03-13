"""Unified semantic reasoner for the query domain."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, cast

from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from apps.core.src.agent.graphs.query.models import (
    Filters,
    PendingClarificationState,
    QueryExecutionContract,
    QueryExtractionResult,
    QueryResultItem,
    ResultSurface,
    SurfaceType,
    TimeRange,
)
from apps.core.src.agent.graphs.query.prompts.main import QUERY_SEMANTIC_REASONER_PROMPT
from apps.core.src.agent.graphs.query.services.continuity import ContinuationClassifier
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_END_SESSION_EXACT = {
    "cancel",
    "abort",
    "stop",
    "nevermind",
    "never mind",
    "thanks",
    "thank you",
    "i'm done",
    "im done",
    "done",
}
_TIME_REPLY_RE = re.compile(r"(last|past)\s+\d{1,3}\s+(day|days|week|weeks|month|months)")
_DETAIL_VIEW_EXACT = {
    "show details",
    "details",
    "view details",
    "tell me more",
    "more details",
}
_RECEIPT_EXACT = {
    "receipt",
    "show receipt",
    "get receipt",
    "i need receipt",
    "proof",
    "proof of payment",
}
_ISSUE_EXACT = {
    "issue",
    "report issue",
    "problem",
    "report a problem",
}


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
    time_period: str | None = Field(default=None)

    continuation_type: Literal[
        "show_more",
        "time_delta",
        "filter_delta",
        "expand",
        "drill_down",
        "recipient_drill_down",
        "aggregate",
        "unclear",
        "end_session",
        "new_query",
    ] | None = Field(default=None)
    delta_type: Literal["filter", "time", "limit", "reference", "none"] | None = Field(default=None)
    time_range: TimeRange | None = Field(default=None)
    filters: Filters | None = Field(default=None)
    result_limit: int | None = Field(default=None)
    result_reference: Literal["latest", "oldest"] | None = Field(default=None)
    drill_down_index: int | None = Field(default=None)
    drill_down_action: Literal["view_details", "get_receipt", "report_issue", "re_transfer"] | None = Field(
        default=None
    )
    recipient_name: str | None = Field(default=None)
    end_session_response: str | None = Field(default=None)
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
        self.llm = llm
        self.structured_llm = cast(Any, llm).with_structured_output(QuerySemanticDecision)
        self._continuation_classifier = ContinuationClassifier(llm)

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
            confidence=decision.confidence,
            reason=decision.reason,
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
        return " ".join(text.lower().strip().split())

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

    @classmethod
    def _looks_like_end_session(cls, text: str) -> bool:
        return cls._normalize(text) in _END_SESSION_EXACT

    @classmethod
    def _looks_like_time_reply(cls, text: str) -> bool:
        normalized = cls._normalize(text).rstrip("?.!,")
        if normalized in {
            "today",
            "yesterday",
            "this week",
            "last week",
            "this month",
            "last month",
            "this year",
            "last year",
        }:
            return True
        return bool(_TIME_REPLY_RE.fullmatch(normalized))

    @classmethod
    def _deterministic_surface_action(
        cls,
        *,
        normalized: str,
        surface: ResultSurface | None,
        language: str,
    ) -> QuerySemanticDecision | None:
        if surface is None or surface.type not in {SurfaceType.SINGLE_ITEM, SurfaceType.LIST}:
            return None

        if normalized in _DETAIL_VIEW_EXACT:
            return QuerySemanticDecision(
                decision="continuation",
                confidence=1.0,
                reason="deterministic_view_details",
                continuation_type="drill_down",
                drill_down_index=0,
                drill_down_action="view_details",
            )
        if normalized in _RECEIPT_EXACT:
            return QuerySemanticDecision(
                decision="continuation",
                confidence=1.0,
                reason="deterministic_receipt",
                continuation_type="drill_down",
                drill_down_index=0,
                drill_down_action="get_receipt",
            )
        if normalized in _ISSUE_EXACT:
            return QuerySemanticDecision(
                decision="continuation",
                confidence=1.0,
                reason="deterministic_report_issue",
                continuation_type="drill_down",
                drill_down_index=0,
                drill_down_action="report_issue",
            )
        return None

    def _continuation_guardrail_decision(self, context: SemanticReasonerContext) -> QuerySemanticDecision | None:
        guarded = self._continuation_classifier._guardrail_classify(
            message=context.message,
            today=context.today.isoformat(),
            items=context.items,
            surface=context.surface,
            language=context.language,
        )
        if guarded is None:
            return None

        continuation_type, data = guarded
        if continuation_type == "end_session":
            return QuerySemanticDecision(
                decision="end_session",
                confidence=data.get("confidence"),
                reason=data.get("reason"),
                end_session_response=data.get("end_session_response"),
            )
        if continuation_type in {"new_query", "aggregate"}:
            return None

        return QuerySemanticDecision(
            decision="continuation",
            confidence=data.get("confidence"),
            reason=data.get("reason"),
            continuation_type=cast(Any, continuation_type),
            delta_type=cast(Any, data.get("delta_type")),
            time_range=data.get("time_range"),
            filters=data.get("filters"),
            result_limit=data.get("result_limit"),
            result_reference=data.get("result_reference"),
            drill_down_index=data.get("drill_down_index"),
            drill_down_action=data.get("drill_down_action"),
            recipient_name=data.get("recipient_name"),
        )

    async def reason(self, context: SemanticReasonerContext) -> QuerySemanticDecision:
        normalized = self._normalize(context.message)

        if context.session_mode == "pending_clarification":
            if self._looks_like_end_session(context.message):
                decision = self._annotate_decision(
                    context_mode=context.session_mode,
                    decision=QuerySemanticDecision(decision="end_session", confidence=1.0, reason="deterministic_end"),
                    llm_used=False,
                )
                self._log_reasoner_decision(context_mode=context.session_mode, decision=decision, llm_used=False)
                return decision
            if self._looks_like_time_reply(context.message):
                decision = self._annotate_decision(
                    context_mode=context.session_mode,
                    decision=QuerySemanticDecision(
                        decision="clarification_answer",
                        confidence=0.99,
                        reason="deterministic_time_reply",
                        time_period=normalized,
                    ),
                    llm_used=False,
                )
                self._log_reasoner_decision(context_mode=context.session_mode, decision=decision, llm_used=False)
                return decision
        elif context.session_mode == "active_result":
            deterministic_surface = self._deterministic_surface_action(
                normalized=normalized,
                surface=context.surface,
                language=context.language,
            )
            if deterministic_surface is not None:
                deterministic_surface = self._annotate_decision(
                    context_mode=context.session_mode,
                    decision=deterministic_surface,
                    llm_used=False,
                    deterministic_surface_action=deterministic_surface.drill_down_action,
                )
                logger.info(
                    "query_surface_action_deterministic",
                    reasoner_context_mode=context.session_mode,
                    action=deterministic_surface.drill_down_action,
                    reason=deterministic_surface.reason,
                )
                self._log_reasoner_decision(
                    context_mode=context.session_mode,
                    decision=deterministic_surface,
                    llm_used=False,
                )
                return deterministic_surface

            guardrail_decision = self._continuation_guardrail_decision(context)
            if guardrail_decision is not None:
                guardrail_decision = self._annotate_decision(
                    context_mode=context.session_mode,
                    decision=guardrail_decision,
                    llm_used=False,
                )
                self._log_reasoner_decision(
                    context_mode=context.session_mode,
                    decision=guardrail_decision,
                    llm_used=False,
                )
                return guardrail_decision

        prompt = QUERY_SEMANTIC_REASONER_PROMPT.format(
            today=context.today.isoformat(),
            language=context.language,
            session_mode=context.session_mode,
            message=context.message,
            current_query=self._serialize(
                context.query_contract.normalized_query if context.query_contract is not None else None
            ),
            pending_clarification=self._serialize(context.pending_clarification),
            surface_type=context.surface.type.value if context.surface is not None else "none",
            surface_context=self._serialize(context.surface.context if context.surface is not None else None),
            items_section=self._serialize(context.items or []),
        )

        try:
            decision = await self.structured_llm.ainvoke(prompt)
            if decision.extraction is not None:
                decision.extraction.raw_query = decision.extraction.raw_query or context.message
            decision = self._annotate_decision(
                context_mode=context.session_mode,
                decision=decision,
                llm_used=True,
            )
            self._log_reasoner_decision(context_mode=context.session_mode, decision=decision, llm_used=True)
            return decision
        except Exception as exc:
            logger.error("query_semantic_reasoner_failed", error=str(exc))
            if context.session_mode == "none":
                decision = self._annotate_decision(
                    context_mode=context.session_mode,
                    decision=QuerySemanticDecision(
                        decision="fresh_query",
                        confidence=0.0,
                        reason="fallback_empty_fresh_query",
                        extraction=QueryExtractionResult(raw_query=context.message),
                    ),
                    llm_used=False,
                )
                self._log_reasoner_decision(context_mode=context.session_mode, decision=decision, llm_used=False)
                return decision
            decision = self._annotate_decision(
                context_mode=context.session_mode,
                decision=QuerySemanticDecision(
                    decision="new_query",
                    confidence=0.0,
                    reason="fallback_new_query",
                    extraction=QueryExtractionResult(raw_query=context.message),
                ),
                llm_used=False,
            )
            self._log_reasoner_decision(context_mode=context.session_mode, decision=decision, llm_used=False)
            return decision
