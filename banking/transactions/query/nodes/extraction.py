"""Extraction step for query pipeline."""

from datetime import date
from typing import Any

from langchain_core.runnables import Runnable

import banking.transactions.query.continuations.compiler_paths as compiler_paths
import banking.transactions.query.continuations.extraction_paths as extraction_paths
import banking.transactions.query.continuations.pending_clarification as pending_clarification
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transactions.query.continuations.time_rescope import is_direct_time_rescope_message
from banking.transactions.query.contracts import SurfaceView, SurfaceViewMode
from banking.transactions.query.grounding.frames import (
    restore_query_frames,
)
from banking.transactions.query.models.domain import (
    QueryExecutionContract,
    QueryFrame,
    QueryIntent,
    QueryResultItem,
    TimeRange,
)
from banking.transactions.query.models.extraction import (
    PendingClarificationState,
    QueryExtractionResult,
    ResolverOutcome,
    TimeReference,
)
from banking.transactions.query.pipeline import QueryStep
from banking.transactions.query.services.parsing.parser import QueryParser
from banking.transactions.query.services.reasoning.models import SemanticReasonerContext
from banking.transactions.query.services.reasoning.reasoner import QuerySemanticReasoner
from shared.utils.logging import get_logger

logger = get_logger(__name__)
extraction_paths.logger = logger
pending_clarification.logger = logger
compiler_paths.logger = logger


class ExtractionStep(QueryStep):
    """Extracts intent and parameters for query."""

    _LOW_CONFIDENCE_THRESHOLD = 0.45
    _COMPILER_SAFE_CONFIDENCE_THRESHOLD = 0.6

    def __init__(self, llm: Runnable):
        self.parser = QueryParser(llm)
        self.reasoner = QuerySemanticReasoner(llm)

    @staticmethod
    def _resolver_outcome_trace(outcome: ResolverOutcome | None) -> str:
        if outcome == ResolverOutcome.OK:
            return "ok"
        if outcome == ResolverOutcome.NEEDS_INPUT:
            return "clarify"
        if outcome == ResolverOutcome.NEGOTIATED:
            return "negotiated"
        return "failed"

    @staticmethod
    def _semantic_trace_updates(decision: Any) -> dict[str, Any]:
        return {
            "_query_semantic_decision": getattr(decision, "decision", None),
            "_query_semantic_context_mode": getattr(decision, "semantic_context_mode", None),
            "_query_semantic_llm_used": getattr(decision, "semantic_llm_used", None),
            "_query_deterministic_surface_action": getattr(decision, "deterministic_surface_action", None),
            "_query_llm_calls_used": getattr(decision, "llm_calls_used", None),
            "_query_single_llm_invariant": getattr(decision, "single_llm_invariant", None),
            "_query_reasoner_schema": getattr(decision, "semantic_reasoner_schema", None),
        }

    @staticmethod
    def _append_query_session_transition(updates: dict[str, Any], transition: str) -> dict[str, Any]:
        updates["_query_session_transition"] = transition
        if transition == "replace_session_new_query":
            updates["flow_state"] = "executing"
        return updates

    @staticmethod
    def _validated_query_contract(
        raw_contract: QueryExecutionContract | dict[str, Any] | None,
    ) -> QueryExecutionContract | None:
        if isinstance(raw_contract, QueryExecutionContract):
            return raw_contract
        if isinstance(raw_contract, dict):
            try:
                return QueryExecutionContract.model_validate(raw_contract)
            except Exception:
                return None
        return None

    @staticmethod
    def _has_supported_followup_query_signal(query_contract: QueryExecutionContract) -> bool:
        if query_contract.intent != QueryIntent.TRANSACTION_LIST:
            return True

        filters = query_contract.filters
        if filters is not None and any(
            (
                bool(filters.transaction_type),
                bool(filters.merchant),
                bool(filters.category),
                filters.min_amount is not None,
                filters.max_amount is not None,
                bool(filters.exclude),
                bool(filters.account_filter),
            )
        ):
            return True

        return any(
            (
                query_contract.aggregation is not None,
                query_contract.result_limit is not None,
                query_contract.result_reference is not None,
                bool(query_contract.account_name),
                query_contract.amount_check is not None,
                bool(query_contract.item_name),
            )
        )

    @staticmethod
    def _has_extraction_query_signal(extraction: QueryExtractionResult) -> bool:
        if extraction.intent != extraction.intent.TRANSACTION_LIST:
            return True

        filters = extraction.filters
        if any(
            (
                bool(filters.recipient),
                filters.min_amount is not None,
                filters.max_amount is not None,
                bool(filters.category),
                bool(filters.transaction_type),
                bool(filters.bank),
                bool(filters.narration_keyword),
            )
        ):
            return True

        return any(
            (
                extraction.aggregation is not None,
                extraction.comparison is not None,
                extraction.result_limit is not None,
                extraction.result_reference is not None,
                extraction.time_range.reference_type != TimeReference.UNSPECIFIED,
            )
        )

    def _compiler_safe_extraction(
        self,
        *,
        extraction: QueryExtractionResult | None,
        confidence: float | None,
        has_original_scope: bool = False,
    ) -> QueryExtractionResult | None:
        safe_extraction, _ = self._compiler_safe_extraction_decision(
            extraction=extraction,
            confidence=confidence,
            has_original_scope=has_original_scope,
        )
        return safe_extraction

    def _compiler_safe_extraction_decision(
        self,
        *,
        extraction: QueryExtractionResult | None,
        confidence: float | None,
        has_original_scope: bool = False,
    ) -> tuple[QueryExtractionResult | None, str]:
        if extraction is None:
            return None, "missing_extraction"
        if self.parser.looks_like_support_problem_statement(extraction.raw_query):
            return None, "support_problem_signal"
        if confidence is not None and confidence < self._COMPILER_SAFE_CONFIDENCE_THRESHOLD:
            return None, "low_confidence"
        if extraction.ambiguities:
            return None, "has_ambiguities"
        if not self._has_extraction_query_signal(extraction):
            return None, "weak_query_signal"
        if has_original_scope and extraction.time_range.reference_type == TimeReference.UNSPECIFIED:
            return None, "followup_requires_explicit_scope"
        return extraction, "compiler_safe"

    @staticmethod
    def _is_income_vs_spending_followup(*, message: str, query_contract: QueryExecutionContract | None) -> bool:
        if query_contract is None or query_contract.intent not in {
            QueryIntent.TRANSACTION_LIST,
            QueryIntent.ANALYTICS_SUMMARY,
        }:
            return False

        normalized = f" {message.lower()} "
        has_compare = any(token in normalized for token in (" compare ", " versus ", " vs "))
        if not has_compare:
            return False

        income_tokens = (" income ", " credit ", " credits ", " inflow ", " inflows ", " came in ", " coming in ")
        has_income = any(token in normalized for token in income_tokens)

        spending_tokens = (
            " spending ", " spend ", " spent ", " debit ", " debits ",
            " outflow ", " outflows ", " went out ", " going out "
        )
        has_spending = any(token in normalized for token in spending_tokens)

        # If it has both, or it's comparing to the opposite of the current active filter
        active_type = getattr(query_contract.filters, "transaction_type", None) if query_contract.filters else None
        if has_income and has_spending:
            return True
        if active_type == "credit" and has_spending:
            return True
        if active_type == "debit" and has_income:
            return True

        return False

    @staticmethod
    def _ambiguous_followup_updates(*, locale: str, session: dict[str, Any]) -> dict[str, Any]:
        return {
            "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
            "response": render_message("query.clarify.unsure_rephrase", locale),
            "flow_state": "parsing",
            "session_active": True,
            "pending_clarification": None,
            "show_expanded": bool(session.get("show_expanded", False)),
            "current_page": session.get("current_page", 0),
        }

    @staticmethod
    def _compose_conversational_reply(decision: Any, *, language: str) -> str:
        response_text = (getattr(decision, "response_text", None) or "").strip()
        contextual_hint = (getattr(decision, "contextual_hint", None) or "").strip()
        if not response_text:
            response_text = render_message("conversational.checkin", language)
        if contextual_hint:
            return f"{response_text}\n\n{contextual_hint}"
        return response_text

    def _load_session_query_contract(self, session: dict[str, Any]) -> QueryExecutionContract | None:
        raw_contract = session.get("query_contract")
        if isinstance(raw_contract, QueryExecutionContract):
            return raw_contract
        if isinstance(raw_contract, dict):
            try:
                return QueryExecutionContract.model_validate(raw_contract)
            except Exception:
                return None
        return None

    def _load_pending_clarification(self, session: dict[str, Any]) -> PendingClarificationState | None:
        raw_pending = session.get("pending_clarification")
        if isinstance(raw_pending, PendingClarificationState):
            return raw_pending
        if isinstance(raw_pending, dict):
            try:
                return PendingClarificationState.model_validate(raw_pending)
            except Exception:
                return None
        return None

    def _load_query_frames(self, session: dict[str, Any]) -> list[QueryFrame]:
        return restore_query_frames(session.get("query_frames"))

    @staticmethod
    def _resolve_end_session_response(decision: Any, *, locale: str) -> str:
        end_session_kind = getattr(decision, "end_session_kind", None)
        if end_session_kind == "dismissive":
            return render_message("query.session.dismissive_goodbye", locale)
        if getattr(decision, "end_session_response", None):
            return str(decision.end_session_response)
        return render_message("query.session.goodbye", locale)

    @staticmethod
    def _build_reasoner_context(
        *,
        message: str,
        today: date,
        language: str,
        state: dict[str, Any],
        query_contract: QueryExecutionContract | None = None,
        items: list[QueryResultItem] | None = None,
        surface_view: SurfaceView | None = None,
        query_frames: list[QueryFrame] | None = None,
        pending_clarification: PendingClarificationState | None = None,
    ) -> SemanticReasonerContext:
        return SemanticReasonerContext(
            message=message,
            today=today,
            language=language,
            query_contract=query_contract,
            items=items,
            surface_view=surface_view,
            query_frames=query_frames,
            pending_clarification=pending_clarification,
            stashed_sessions=state.get("stashed_sessions"),
            turn_id=state.get("turn_id"),
            inbound_message_id=state.get("inbound_message_id"),
        )

    @staticmethod
    def _log_query_trace(
        *,
        state: dict[str, Any],
        phase: str,
        latency_ms: float,
        outcome: str,
        resolution_source: str | None = None,
        semantic_decision: str | None = None,
        continuation_type: str | None = None,
        llm_calls_used: int | None = None,
        single_llm_invariant: bool | None = None,
        reasoner_schema: str | None = None,
    ) -> None:
        logger.info(
            "query_trace",
            turn_id=state.get("turn_id"),
            inbound_message_id=state.get("inbound_message_id"),
            query_phase=phase,
            latency_ms=round(latency_ms, 2),
            outcome=outcome,
            session_mode=state.get("_query_semantic_context_mode"),
            semantic_decision=semantic_decision,
            continuation_type=continuation_type,
            resolution_source=resolution_source,
            llm_calls_used=llm_calls_used,
            single_llm_invariant=single_llm_invariant,
            reasoner_schema=reasoner_schema,
        )

    @staticmethod
    def _log_time_rescope_recovery(
        *,
        trigger_reason: str,
        recovered: bool,
        session_has_query_contract: bool,
        resolved_time_range: TimeRange | None = None,
        skip_reason: str | None = None,
        preserved_query_shape: bool | None = None,
    ) -> None:
        logger.info(
            "query_continuation_resolution",
            path="time_rescope_recovery",
            trigger_reason=trigger_reason,
            recovered=recovered,
            session_has_query_contract=session_has_query_contract,
            resolved_time_range=resolved_time_range is not None,
            resolved_time_start=resolved_time_range.start.isoformat() if resolved_time_range is not None else None,
            resolved_time_end=resolved_time_range.end.isoformat() if resolved_time_range is not None else None,
            preserved_query_shape=preserved_query_shape,
            skip_reason=skip_reason,
        )

    @staticmethod
    def _is_direct_time_rescope_message(message: str, *, today: date) -> bool:
        return is_direct_time_rescope_message(message, today=today)

    @staticmethod
    def _should_ignore_grounded_query_for_aggregate(
        *,
        grounded_updates: dict[str, Any],
        session_query_contract: QueryExecutionContract | None,
        continuation_type: str,
    ) -> bool:
        if continuation_type != "aggregate" or session_query_contract is None:
            return False
        if session_query_contract.intent != QueryIntent.TRANSACTION_LIST:
            return False

        grounded_contract = grounded_updates.get("query_contract")
        if not isinstance(grounded_contract, QueryExecutionContract):
            return False

        return grounded_contract.intent == QueryIntent.TRANSACTION_LIST

    @staticmethod
    def _should_attach_resolver_message(result: Any) -> bool:
        if getattr(result, "outcome", None) != ResolverOutcome.NEGOTIATED:
            return True

        extraction = getattr(result, "extraction", None)
        if extraction is None:
            return True

        filters = getattr(extraction, "filters", None)
        narration_keyword = getattr(filters, "narration_keyword", None) if filters is not None else None
        return bool(isinstance(narration_keyword, str) and narration_keyword.strip())

    @staticmethod
    def _is_single_item_surface(surface_view: SurfaceView | None = None) -> bool:
        return bool(
            surface_view
            and surface_view.mode == SurfaceViewMode.DIRECT_ANSWER
            and isinstance(surface_view.context, dict)
            and surface_view.context.get("type") == "single_transaction"
        )

    def _log_single_item_followup(
        self,
        *,
        surface_view: SurfaceView | None = None,
        continuation_type: str | None,
        followup_outcome: str,
        decision: str | None = None,
    ) -> None:
        if not self._is_single_item_surface(surface_view):
            return
        logger.info(
            "query_single_item_followup",
            surface_type="single_item",
            session_mode="active_result",
            continuation_type=continuation_type,
            followup_outcome=followup_outcome,
            semantic_decision=decision,
        )

    async def run(self, state: dict[str, Any], worker_context: Any = None) -> TransactionResult:
        """Run extraction logic."""
        del worker_context
        query_session = state.get("query_session")
        locale = LocaleManager.normalize(state.get("language")).value

        updates: dict[str, Any] = {}
        force_new_query = bool(state.get("force_new_query"))

        # If we have an active session, check for continuity
        if force_new_query:
            updates = await self._parse_new_query(state)
        elif query_session and query_session.get("session_active") and self._load_pending_clarification(query_session):
            updates = await pending_clarification.handle_pending_clarification(self, state, query_session)
        elif query_session and query_session.get("session_active"):
            updates = await self._handle_continuation(state, query_session)
        else:
            updates = await self._parse_new_query(state)

        # Check if we need to stop for input (clarification/negotiation)
        if updates.get("transaction_outcome") == TransactionOutcome.NEEDS_INPUT:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                response=updates.get("response"),
                patch=updates,
            )

        # Check if session ended
        if updates.get("flow_state") == "complete":
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=updates.get("response", render_message("query.session.completed", locale)),
                patch=updates,
            )

        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch=updates,
        )

    async def _handle_continuation(self, state: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
        return await extraction_paths.handle_continuation(self, state, session)

    async def _parse_new_query(self, state: dict[str, Any]) -> dict[str, Any]:
        return await compiler_paths.parse_new_query(self, state)

    async def _parse_reasoner_extraction_to_updates(
        self,
        decision: Any,
        *,
        state: dict[str, Any],
        today: date,
        language: str,
    ) -> dict[str, Any]:
        return await compiler_paths.parse_reasoner_extraction_to_updates(
            self,
            decision,
            state=state,
            today=today,
            language=language,
        )

    def _parse_result_to_updates(
        self,
        result: Any,
        *,
        state: dict[str, Any],
        today: date,
        language: str,
        message_override: str | None = None,
    ) -> dict[str, Any]:
        return compiler_paths.parse_result_to_updates(
            self,
            result,
            state=state,
            today=today,
            language=language,
            message_override=message_override,
        )
