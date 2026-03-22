"""Extraction step for query pipeline."""

from datetime import date
from time import perf_counter
from typing import Any

from langchain_core.runnables import Runnable

from apps.core.src.agent.graphs.query.models import (
    Aggregation,
    AmbiguityCode,
    NormalizedQuery,
    PendingClarificationState,
    QueryExecutionContract,
    QueryExtractionResult,
    QueryFrame,
    QueryIntent,
    QueryResultItem,
    ResolverOutcome,
    ResultSurface,
    SurfaceType,
    TimeRange,
    TimeReference,
)
from apps.core.src.agent.graphs.query.pipeline import QueryStep
from apps.core.src.agent.graphs.query.services.continuity import (
    apply_filter_delta,
    apply_time_delta,
)
from apps.core.src.agent.graphs.query.services.grounding import (
    build_grounded_query_contract,
    build_memory_answer,
    restore_query_frames,
)
from apps.core.src.agent.graphs.query.services.parser import QueryParser
from apps.core.src.agent.graphs.query.services.reasoner import (
    QuerySemanticReasoner,
    SemanticReasonerContext,
)
from apps.core.src.agent.graphs.query.utils.timezone import lagos_today
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.i18n import LocaleManager, render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


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
        }

    @staticmethod
    def _append_query_session_transition(updates: dict[str, Any], transition: str) -> dict[str, Any]:
        updates["_query_session_transition"] = transition
        return updates

    @staticmethod
    def _validated_query_contract(raw_contract: QueryExecutionContract | dict[str, Any] | None) -> QueryExecutionContract | None:
        if isinstance(raw_contract, QueryExecutionContract):
            return raw_contract
        if isinstance(raw_contract, dict):
            try:
                return QueryExecutionContract.model_validate(raw_contract)
            except Exception:
                return None
        return None

    @staticmethod
    def _has_supported_followup_query_signal(query: NormalizedQuery) -> bool:
        if query.intent != QueryIntent.TRANSACTION_LIST:
            return True

        filters = query.filters
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
                query.aggregation is not None,
                query.result_limit is not None,
                query.result_reference is not None,
                bool(query.account_name),
                query.amount_check is not None,
                bool(query.item_name),
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
        original_query: NormalizedQuery | None = None,
    ) -> QueryExtractionResult | None:
        safe_extraction, _ = self._compiler_safe_extraction_decision(
            extraction=extraction,
            confidence=confidence,
            original_query=original_query,
        )
        return safe_extraction

    def _compiler_safe_extraction_decision(
        self,
        *,
        extraction: QueryExtractionResult | None,
        confidence: float | None,
        original_query: NormalizedQuery | None = None,
    ) -> tuple[QueryExtractionResult | None, str]:
        if extraction is None:
            return None, "missing_extraction"
        if confidence is not None and confidence < self._COMPILER_SAFE_CONFIDENCE_THRESHOLD:
            return None, "low_confidence"
        if extraction.ambiguities:
            return None, "has_ambiguities"
        if not self._has_extraction_query_signal(extraction):
            return None, "weak_query_signal"
        if original_query is not None and extraction.time_range.reference_type == TimeReference.UNSPECIFIED:
            return None, "followup_requires_explicit_scope"
        return extraction, "compiler_safe"

    async def _maybe_recover_supported_followup_query(
        self,
        *,
        state: dict[str, Any],
        today: date,
        language: str,
        original_query: NormalizedQuery | None = None,
        reasoner_extraction: QueryExtractionResult | None = None,
        reasoner_confidence: float | None = None,
    ) -> dict[str, Any] | None:
        compiler_safe_extraction, compiler_safe_reason = self._compiler_safe_extraction_decision(
            extraction=reasoner_extraction,
            confidence=reasoner_confidence,
            original_query=original_query,
        )
        resolution_source = "parser_parse"
        if compiler_safe_extraction is not None:
            if not compiler_safe_extraction.raw_query:
                compiler_safe_extraction = compiler_safe_extraction.model_copy(
                    update={"raw_query": state.get("message", "")}
                )
            parsed_result = self.parser.compile_extraction(
                compiler_safe_extraction,
                today=today,
                language=language,
            )
            resolution_source = "reasoner_extraction_compile"
        else:
            parsed_result = await self.parser.parse(state.get("message", ""), today=today, language=language)
            logger.info(
                "query_continuation_resolution",
                path="fallback_parse_supported_query",
                recovered=False,
                skip_reason=compiler_safe_reason,
                resolution_source=resolution_source,
            )
        if parsed_result.outcome != ResolverOutcome.OK or parsed_result.extraction is None:
            logger.info(
                "query_continuation_resolution",
                path="fallback_parse_supported_query",
                recovered=False,
                skip_reason="parser_not_ok" if resolution_source == "parser_parse" else "compiler_not_ok",
                parser_outcome=parsed_result.outcome.value if hasattr(parsed_result.outcome, "value") else str(parsed_result.outcome),
                resolution_source=resolution_source,
            )
            return None

        query_contract: QueryExecutionContract | dict[str, Any] | None = parsed_result.query_contract
        if isinstance(query_contract, dict):
            try:
                query_contract = QueryExecutionContract.model_validate(query_contract)
            except Exception:
                query_contract = None
        if not isinstance(query_contract, QueryExecutionContract):
            logger.info(
                "query_continuation_resolution",
                path="fallback_parse_supported_query",
                recovered=False,
                skip_reason="missing_query_contract",
                resolution_source=resolution_source,
            )
            return None

        if not self._has_supported_followup_query_signal(query_contract.normalized_query):
            logger.info(
                "query_continuation_resolution",
                path="fallback_parse_supported_query",
                recovered=False,
                skip_reason="time_only_or_weak_query_signal",
                parsed_intent=query_contract.normalized_query.intent.value,
                resolution_source=resolution_source,
            )
            return None

        extraction = parsed_result.extraction
        parsed_query = query_contract.normalized_query
        if (
            original_query is not None
            and extraction.time_range.reference_type == TimeReference.UNSPECIFIED
        ):
            logger.info(
                "query_continuation_resolution",
                path="fallback_parse_supported_query",
                recovered=False,
                skip_reason="followup_reparse_requires_explicit_scope",
                parsed_intent=parsed_query.intent.value,
                resolution_source=resolution_source,
            )
            return None

        logger.info(
            "query_continuation_resolution",
            path="fallback_parse_supported_query",
            recovered=True,
            parsed_intent=parsed_query.intent.value,
            resolution_source=resolution_source,
        )
        recovered_updates = self._parse_result_to_updates(parsed_result, state=state, today=today, language=language)
        return self._append_query_session_transition(recovered_updates, "replace_session_new_query")

    async def _compile_aggregate_continuation_updates(
        self,
        *,
        decision: Any,
        state: dict[str, Any],
        today: date,
        language: str,
        original_query: NormalizedQuery | None,
    ) -> dict[str, Any] | None:
        extraction = getattr(decision, "extraction", None)
        if extraction is not None and not extraction.raw_query:
            extraction = extraction.model_copy(update={"raw_query": state.get("message", "")})

        if original_query is None:
            if extraction is None:
                return None
            patched_decision = decision.model_copy(update={"extraction": extraction}) if hasattr(decision, "model_copy") else decision
            return await self._parse_reasoner_extraction_to_updates(
                patched_decision,
                state=state,
                today=today,
                language=language,
            )

        extracted_query: NormalizedQuery | None = None
        if extraction is not None:
            extracted_query = self.parser.convert_to_normalized(extraction, today=today)
            if extracted_query.intent in {
                QueryIntent.TIME_COMPARISON,
                QueryIntent.BENEFICIARY_SUMMARY,
                QueryIntent.AFFORDABILITY,
            }:
                patched_decision = (
                    decision.model_copy(update={"extraction": extraction}) if hasattr(decision, "model_copy") else decision
                )
                return await self._parse_reasoner_extraction_to_updates(
                    patched_decision,
                    state=state,
                    today=today,
                    language=language,
                )

        aggregate_query = original_query.model_copy(deep=True)
        if extracted_query is not None and extracted_query.filters is not None:
            aggregate_query = apply_filter_delta(aggregate_query, extracted_query.filters)
        aggregate_query.intent = QueryIntent.ANALYTICS_SUMMARY
        if extracted_query is not None and extracted_query.aggregation is not None:
            aggregate_query.aggregation = extracted_query.aggregation.model_copy(deep=True)
        elif self._is_income_vs_spending_followup(message=state.get("message", ""), original_query=original_query):
            aggregate_query.aggregation = Aggregation(type="breakdown", group_by="transaction_type")
            logger.info(
                "query_continuation_resolution",
                path="aggregate_income_vs_spending_fallback",
                recovered=True,
                original_intent=original_query.intent.value,
            )
        else:
            aggregate_query.aggregation = Aggregation(type="sum")
        aggregate_query.result_limit = extracted_query.result_limit if extracted_query is not None else None
        aggregate_query.result_reference = extracted_query.result_reference if extracted_query is not None else None

        query_contract = QueryExecutionContract.from_normalized_query(
            aggregate_query,
            continuation_type=getattr(decision, "continuation_type", None),
            continuation_delta_type=getattr(decision, "delta_type", None),
        )
        logger.info(
            "query_aggregate_continuation_compiled",
            source="reasoner_extraction" if extracted_query is not None else "active_query_scope_default",
            aggregation_type=query_contract.aggregation.type if query_contract.aggregation is not None else None,
            time_start=query_contract.time_start.isoformat(),
            time_end=query_contract.time_end.isoformat(),
            has_filters=bool(query_contract.normalized_query.filters),
        )
        return {
            "query_contract": query_contract,
            "resolver_message": None,
            "flow_state": "executing",
            "current_page": 0,
            "session_active": True,
            "pending_clarification": None,
            "show_expanded": False,
        }

    @staticmethod
    def _is_income_vs_spending_followup(*, message: str, original_query: NormalizedQuery | None) -> bool:
        if original_query is None or original_query.intent != QueryIntent.TRANSACTION_LIST:
            return False

        filters = original_query.filters
        if filters is not None and filters.transaction_type is not None:
            return False

        normalized = f" {message.lower()} "
        has_compare = any(token in normalized for token in (" compare ", " versus ", " vs "))
        if not has_compare:
            return False

        has_income = any(token in normalized for token in (" income ", " credit ", " credits ", " inflow ", " inflows "))
        has_spending = any(
            token in normalized
            for token in (" spending ", " spend ", " spent ", " debit ", " debits ", " outflow ", " outflows ")
        )
        return has_income and has_spending

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
    def _log_query_trace(
        *,
        state: dict[str, Any],
        phase: str,
        latency_ms: float,
        outcome: str,
        resolution_source: str | None = None,
        semantic_decision: str | None = None,
        continuation_type: str | None = None,
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

    async def _maybe_recover_time_rescope_continuation(
        self,
        *,
        trigger_reason: str,
        decision: Any,
        state: dict[str, Any],
        session: dict[str, Any],
        session_query_contract: QueryExecutionContract | None,
        message: str,
        today: date,
        language: str,
    ) -> dict[str, Any] | None:
        original_query = session_query_contract.normalized_query if session_query_contract is not None else None
        if original_query is None:
            self._log_time_rescope_recovery(
                trigger_reason=trigger_reason,
                recovered=False,
                session_has_query_contract=False,
                skip_reason="missing_query_contract",
            )
            return None

        resolved_time_range, clarification_message = await self._resolve_time_delta_range(
            decision=decision,
            message=message,
            today=today,
            language=language,
            state=state,
        )
        if resolved_time_range is None:
            self._log_time_rescope_recovery(
                trigger_reason=trigger_reason,
                recovered=False,
                session_has_query_contract=True,
                skip_reason="parser_requested_clarification" if clarification_message else "time_not_resolved",
            )
            return None

        new_query = original_query.model_copy(deep=True)
        new_query.time_range = resolved_time_range
        if decision.result_limit is not None:
            new_query.result_limit = decision.result_limit
        if decision.result_reference is not None:
            new_query.result_reference = decision.result_reference

        preserved_query_shape = (
            new_query.intent == original_query.intent
            and new_query.filters == original_query.filters
            and new_query.aggregation == original_query.aggregation
        )
        self._log_time_rescope_recovery(
            trigger_reason=trigger_reason,
            recovered=True,
            session_has_query_contract=True,
            resolved_time_range=resolved_time_range,
            preserved_query_shape=preserved_query_shape,
        )
        return {
            "flow_state": "executing",
            "continuation_type": "time_delta",
            "continuation_delta_type": decision.delta_type or "time",
            "resolver_message": None,
            "query_contract": QueryExecutionContract.from_normalized_query(
                new_query,
                continuation_type="time_delta",
                continuation_delta_type=decision.delta_type or "time",
            ),
            "current_page": 0,
            "show_expanded": False,
        }

    async def _resolve_time_delta_range(
        self,
        *,
        decision: Any,
        message: str,
        today: date,
        language: str,
        state: dict[str, Any] | None = None,
    ) -> tuple[TimeRange | None, str | None]:
        started_at = perf_counter()
        semantic_decision = getattr(decision, "decision", None)
        continuation_type = getattr(decision, "continuation_type", None)

        if decision.time_range is not None:
            resolved_time_range = decision.time_range
            if state is not None:
                self._log_query_trace(
                    state=state,
                    phase="time_resolution",
                    latency_ms=(perf_counter() - started_at) * 1000.0,
                    outcome="resolved",
                    resolution_source="decision_time_range",
                    semantic_decision=semantic_decision,
                    continuation_type=continuation_type,
                )
            return resolved_time_range, None

        normalized_message = " ".join(message.strip().split())
        if normalized_message:
            parts = normalized_message.split()
            for start in range(len(parts)):
                candidate = " ".join(parts[start:])
                parsed_time_range = self.parser.parse_clarification_time_range(candidate, today=today)
                if parsed_time_range is None:
                    continue
                query_ir = self.parser.build_query_ir_from_extraction(
                    QueryExtractionResult(
                        time_range=parsed_time_range,
                        raw_query=message,
                    ),
                    today=today,
                    language=language,
                )
                if state is not None:
                    self._log_query_trace(
                        state=state,
                        phase="time_resolution",
                        latency_ms=(perf_counter() - started_at) * 1000.0,
                        outcome="resolved",
                        resolution_source="message_suffix_parse",
                        semantic_decision=semantic_decision,
                        continuation_type=continuation_type,
                    )
                return query_ir.time_range, None

        extraction = getattr(decision, "extraction", None)
        if extraction is not None:
            if not extraction.raw_query:
                extraction = extraction.model_copy(update={"raw_query": message})
            result = self.parser.compile_extraction(extraction, today=today, language=language)
            if result.outcome == ResolverOutcome.NEEDS_INPUT:
                return None, result.resolver_message or render_message("query.clarify.default", language)

            query_contract = None
            if isinstance(result.query_contract, dict):
                try:
                    query_contract = QueryExecutionContract.model_validate(result.query_contract)
                except Exception:
                    query_contract = None

            if query_contract and query_contract.normalized_query.time_range is not None:
                if state is not None:
                    self._log_query_trace(
                        state=state,
                        phase="time_resolution",
                        latency_ms=(perf_counter() - started_at) * 1000.0,
                        outcome="resolved",
                        resolution_source="decision_extraction_contract",
                        semantic_decision=semantic_decision,
                        continuation_type=continuation_type,
                    )
                return query_contract.normalized_query.time_range, None

            if result.extraction is not None:
                query_ir = self.parser.build_query_ir_from_extraction(result.extraction, today=today, language=language)
                if state is not None:
                    self._log_query_trace(
                        state=state,
                        phase="time_resolution",
                        latency_ms=(perf_counter() - started_at) * 1000.0,
                        outcome="resolved",
                        resolution_source="decision_extraction_ir",
                        semantic_decision=semantic_decision,
                        continuation_type=continuation_type,
                    )
                return query_ir.time_range, None

        if decision.time_period:
            parsed_time_range = self.parser.parse_clarification_time_range(decision.time_period, today=today)
            if parsed_time_range is None:
                if state is not None:
                    self._log_query_trace(
                        state=state,
                        phase="time_resolution",
                        latency_ms=(perf_counter() - started_at) * 1000.0,
                        outcome="unresolved",
                        resolution_source="decision_time_period_invalid",
                        semantic_decision=semantic_decision,
                        continuation_type=continuation_type,
                    )
                return None, None
            query_ir = self.parser.build_query_ir_from_extraction(
                QueryExtractionResult(
                    time_range=parsed_time_range,
                    raw_query=message,
                ),
                today=today,
                language=language,
            )
            if state is not None:
                self._log_query_trace(
                    state=state,
                    phase="time_resolution",
                    latency_ms=(perf_counter() - started_at) * 1000.0,
                    outcome="resolved",
                    resolution_source="decision_time_period",
                    semantic_decision=semantic_decision,
                    continuation_type=continuation_type,
                )
            return query_ir.time_range, None

        parsed_result = await self.parser.parse(message, today=today, language=language)
        if parsed_result.outcome == ResolverOutcome.NEEDS_INPUT:
            if state is not None:
                self._log_query_trace(
                    state=state,
                    phase="time_resolution",
                    latency_ms=(perf_counter() - started_at) * 1000.0,
                    outcome="clarify",
                    resolution_source="parser_parse",
                    semantic_decision=semantic_decision,
                    continuation_type=continuation_type,
                )
            return None, parsed_result.resolver_message or render_message("query.clarify.default", language)

        parsed_extraction = getattr(parsed_result, "extraction", None)
        parsed_reference_type = (
            parsed_extraction.time_range.reference_type if parsed_extraction and parsed_extraction.time_range else None
        )
        if parsed_reference_type in {TimeReference.EXPLICIT, TimeReference.ALL_TIME}:
            query_contract = None
            if isinstance(parsed_result.query_contract, dict):
                try:
                    query_contract = QueryExecutionContract.model_validate(parsed_result.query_contract)
                except Exception:
                    query_contract = None

            if query_contract and query_contract.normalized_query.time_range is not None:
                if state is not None:
                    self._log_query_trace(
                        state=state,
                        phase="time_resolution",
                        latency_ms=(perf_counter() - started_at) * 1000.0,
                        outcome="resolved",
                        resolution_source="parser_contract",
                        semantic_decision=semantic_decision,
                        continuation_type=continuation_type,
                    )
                return query_contract.normalized_query.time_range, None

            if parsed_extraction is not None and parsed_extraction.time_range is not None:
                query_ir = self.parser.build_query_ir_from_extraction(parsed_extraction, today=today, language=language)
                if state is not None:
                    self._log_query_trace(
                        state=state,
                        phase="time_resolution",
                        latency_ms=(perf_counter() - started_at) * 1000.0,
                        outcome="resolved",
                        resolution_source="parser_ir",
                        semantic_decision=semantic_decision,
                        continuation_type=continuation_type,
                    )
                return query_ir.time_range, None

        if state is not None:
            self._log_query_trace(
                state=state,
                phase="time_resolution",
                latency_ms=(perf_counter() - started_at) * 1000.0,
                outcome="unresolved",
                resolution_source="none",
                semantic_decision=semantic_decision,
                continuation_type=continuation_type,
            )
        return None, None

    def _resolve_grounded_followup(
        self,
        *,
        decision: Any,
        session: dict[str, Any],
        language: str,
    ) -> dict[str, Any] | None:
        answer_mode = getattr(decision, "answer_mode", None)
        if answer_mode is None:
            return None

        query_frames = self._load_query_frames(session)
        frame_ids = getattr(decision, "referenced_frame_ids", None)
        operation = getattr(decision, "grounded_operation", None)

        if answer_mode == "ask_clarify":
            return {
                "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
                "response": render_message("query.clarify.unsure_rephrase", language),
                "flow_state": "parsing",
                "session_active": True,
                "pending_clarification": None,
                "show_expanded": bool(session.get("show_expanded", False)),
                "current_page": session.get("current_page", 0),
            }

        if answer_mode == "memory_answer":
            response = build_memory_answer(
                query_frames=query_frames,
                frame_ids=frame_ids,
                operation=operation,
                language=language,
            )
            if response is None:
                return {
                    "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
                    "response": render_message("query.clarify.unsure_rephrase", language),
                    "flow_state": "parsing",
                    "session_active": True,
                    "pending_clarification": None,
                    "show_expanded": bool(session.get("show_expanded", False)),
                    "current_page": session.get("current_page", 0),
                }
            return {
                "response": response,
                "flow_state": "complete",
                "session_active": True,
                "pending_clarification": None,
                "resolver_message": None,
                "show_expanded": bool(session.get("show_expanded", False)),
                "current_page": session.get("current_page", 0),
            }

        if answer_mode == "grounded_query":
            query_contract = build_grounded_query_contract(
                query_frames=query_frames,
                frame_ids=frame_ids,
                operation=operation,
            )
            if query_contract is None:
                return {
                    "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
                    "response": render_message("query.clarify.unsure_rephrase", language),
                    "flow_state": "parsing",
                    "session_active": True,
                    "pending_clarification": None,
                    "show_expanded": bool(session.get("show_expanded", False)),
                    "current_page": session.get("current_page", 0),
                }
            return {
                "flow_state": "executing",
                "resolver_message": None,
                "query_contract": query_contract,
                "current_page": 0,
                "show_expanded": False,
            }

        return None

    @staticmethod
    def _should_ignore_grounded_query_for_aggregate(
        *,
        grounded_updates: dict[str, Any],
        original_query: NormalizedQuery | None,
        continuation_type: str,
    ) -> bool:
        if continuation_type != "aggregate" or original_query is None:
            return False
        if original_query.intent != QueryIntent.TRANSACTION_LIST:
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
    def _is_single_item_surface(surface: ResultSurface | None) -> bool:
        return bool(
            surface
            and surface.type == SurfaceType.SINGLE_ITEM
            and isinstance(surface.context, dict)
            and surface.context.get("type") == "single_transaction"
        )

    def _log_single_item_followup(
        self,
        *,
        surface: ResultSurface | None,
        continuation_type: str | None,
        followup_outcome: str,
        decision: str | None = None,
    ) -> None:
        if not self._is_single_item_surface(surface):
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
        query_session = state.get("query_session")
        locale = LocaleManager.normalize(state.get("language")).value

        updates: dict[str, Any] = {}
        force_new_query = bool(state.get("force_new_query"))

        # If we have an active session, check for continuity
        if force_new_query:
            updates = await self._parse_new_query(state)
        elif query_session and query_session.get("session_active") and self._load_pending_clarification(query_session):
            updates = await self._handle_pending_clarification(state, query_session)
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

    async def _handle_pending_clarification(self, state: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
        """Resolve a follow-up against an unresolved semantic query state."""
        pending = self._load_pending_clarification(session)
        if pending is None:
            return await self._parse_new_query(state)

        message = state.get("message", "")
        today_state = state.get("today")
        today = today_state if isinstance(today_state, date) else lagos_today()
        locale = LocaleManager.normalize(state.get("language")).value

        decision = await self.reasoner.reason(
            SemanticReasonerContext(
                message=message,
                today=today,
                language=locale,
                pending_clarification=pending,
                query_frames=self._load_query_frames(session),
                turn_id=state.get("turn_id"),
                inbound_message_id=state.get("inbound_message_id"),
            )
        )
        logger.info(
            "query_pending_clarification_resolved",
            decision=decision.decision,
            reason=decision.reason,
            confidence=decision.confidence,
        )

        if decision.decision == "end_session":
            return self._append_query_session_transition({
                "transaction_outcome": TransactionOutcome.OK,
                "response": self._resolve_end_session_response(decision, locale=locale),
                "session_active": False,
                "pending_clarification": None,
                "flow_state": "complete",
                **self._semantic_trace_updates(decision),
            }, "end_query_session")

        if decision.decision == "new_query":
            return await self._parse_reasoner_extraction_to_updates(
                decision,
                state=state,
                today=today,
                language=locale,
            )

        grounded_updates = self._resolve_grounded_followup(
            decision=decision,
            session=session,
            language=locale,
        )
        if grounded_updates is not None:
            grounded_updates.update(self._semantic_trace_updates(decision))
            return grounded_updates

        if decision.decision == "clarification_answer":
            patched_extraction = pending.original_extraction.model_copy(deep=True)
            if decision.time_period:
                parsed_time_range = self.parser.parse_clarification_time_range(decision.time_period, today=today)
                if parsed_time_range is not None:
                    patched_extraction.time_range = parsed_time_range
                    patched_extraction.ambiguities = [
                        ambiguity
                        for ambiguity in patched_extraction.ambiguities
                        if ambiguity.code != AmbiguityCode.TIME_VAGUE
                    ]
            result = self.parser.compile_extraction(
                patched_extraction,
                today=today,
                language=locale,
            )
            updates = self._parse_result_to_updates(result, state=state, today=today, language=locale)
            updates.update(self._semantic_trace_updates(decision))
            return updates

        updates = await self._parse_reasoner_extraction_to_updates(
            decision,
            state=state,
            today=today,
            language=locale,
        )
        updates.update(self._semantic_trace_updates(decision))
        return updates

    async def _handle_continuation(self, state: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
        """Handle possible continuation of previous query."""
        message = state.get("message", "")
        today_state = state.get("today")
        today = today_state if isinstance(today_state, date) else lagos_today()
        session_query_contract = self._load_session_query_contract(session)
        locale = LocaleManager.normalize(state.get("language")).value
        original_query = session_query_contract.normalized_query if session_query_contract else None

        # Reconstruct items for context if available
        items = []
        possible_result = session.get("query_result")

        if possible_result:
            raw_items = []
            if isinstance(possible_result, dict):
                raw_items = possible_result.get("items", [])
            else:
                raw_items = getattr(possible_result, "items", [])

            if raw_items:
                items = [QueryResultItem.model_validate(i) if isinstance(i, dict) else i for i in raw_items]

        raw_surface = session.get("surface")
        surface = ResultSurface.model_validate(raw_surface) if isinstance(raw_surface, dict) else raw_surface
        logger.info(
            "query_continuation_entry",
            has_query_contract=original_query is not None,
            has_surface=bool(session.get("surface")),
            has_query_result=bool(session.get("query_result")),
            current_page=session.get("current_page", 0),
            show_expanded=bool(session.get("show_expanded", False)),
            surface_type=surface.type.value if isinstance(surface, ResultSurface) else None,
        )
        locale = LocaleManager.normalize(state.get("language")).value
        query_frames = self._load_query_frames(session)

        decision = await self.reasoner.reason(
            SemanticReasonerContext(
                message=message,
                today=today,
                language=locale,
                query_contract=session_query_contract,
                items=items,
                surface=surface,
                query_frames=query_frames,
                turn_id=state.get("turn_id"),
                inbound_message_id=state.get("inbound_message_id"),
            )
        )
        cont_type = decision.continuation_type or "unclear"

        logger.info(
            "query_continuation_type",
            type=cont_type,
            confidence=decision.confidence,
            reason=decision.reason,
            semantic_decision=decision.decision,
        )
        logger.info(
            "query_continuation_resolution",
            path="semantic_reasoner",
            semantic_decision=decision.decision,
            continuation_type=cont_type,
            followup_intent=decision.followup_intent,
            delta_type=decision.delta_type,
        )

        if decision.decision == "end_session":
            self._log_single_item_followup(
                surface=surface,
                continuation_type=cont_type,
                followup_outcome="end_session",
                decision=decision.decision,
            )
            return self._append_query_session_transition({
                "transaction_outcome": TransactionOutcome.OK,
                "response": self._resolve_end_session_response(decision, locale=locale),
                "session_active": False,
                "flow_state": "complete",
                **self._semantic_trace_updates(decision),
            }, "end_query_session")

        if decision.decision in {"fresh_query", "new_query", "reinterpret_query"}:
            self._log_single_item_followup(
                surface=surface,
                continuation_type=cont_type,
                followup_outcome="reparse_query",
                decision=decision.decision,
            )
            logger.info(
                "query_continuation_resolution",
                path="semantic_reparse",
                semantic_decision=decision.decision,
            )
            semantic_updates = await self._parse_reasoner_extraction_to_updates(
                decision,
                state=state,
                today=today,
                language=locale,
            )
            semantic_updates.update(self._semantic_trace_updates(decision))
            self._append_query_session_transition(semantic_updates, "replace_session_new_query")
            return semantic_updates

        if decision.decision != "continuation":
            self._log_single_item_followup(
                surface=surface,
                continuation_type=cont_type,
                followup_outcome="fallback_parse_new_query",
                decision=decision.decision,
            )
            logger.info(
                "query_continuation_resolution",
                path="fallback_parse_new_query",
                semantic_decision=decision.decision,
            )
            return await self._parse_new_query(state)

        grounded_updates = self._resolve_grounded_followup(
            decision=decision,
            session=session,
            language=locale,
        )
        if grounded_updates is not None and self._should_ignore_grounded_query_for_aggregate(
            grounded_updates=grounded_updates,
            original_query=original_query,
            continuation_type=cont_type,
        ):
            logger.info(
                "query_grounded_followup_ignored",
                reason="aggregate_requires_new_query_shape",
                grounded_intent=grounded_updates["query_contract"].intent.value,
                original_intent=original_query.intent.value if original_query is not None else None,
            )
            grounded_updates = None
        if grounded_updates is not None:
            if decision.answer_mode == "ask_clarify":
                recovered_updates = await self._maybe_recover_time_rescope_continuation(
                    trigger_reason="grounded_ask_clarify",
                    decision=decision,
                    state=state,
                    session=session,
                    session_query_contract=session_query_contract,
                    message=message,
                    today=today,
                    language=locale,
                )
                if recovered_updates is not None:
                    self._log_single_item_followup(
                        surface=surface,
                        continuation_type="time_delta",
                        followup_outcome="time_rescope_query",
                        decision=decision.decision,
                    )
                    recovered_updates.update(self._semantic_trace_updates(decision))
                    return recovered_updates
            self._log_single_item_followup(
                surface=surface,
                continuation_type=cont_type,
                followup_outcome="clarify" if decision.answer_mode == "ask_clarify" else "grounded_answer",
                decision=decision.decision,
            )
            grounded_updates.update(self._semantic_trace_updates(decision))
            return grounded_updates

        followup_intent = decision.followup_intent or "none"
        if followup_intent not in ("refine_existing", "replace_scope", "continue_pagination", "none"):
            followup_intent = "none"

        if decision.confidence is not None and decision.confidence < self._LOW_CONFIDENCE_THRESHOLD:
            if cont_type not in {"drill_down", "recipient_drill_down", "conversational"}:
                supported_query_updates = await self._maybe_recover_supported_followup_query(
                    state=state,
                    today=today,
                    language=locale,
                    original_query=original_query,
                    reasoner_extraction=getattr(decision, "extraction", None),
                    reasoner_confidence=decision.confidence,
                )
                if supported_query_updates is not None:
                    supported_query_updates.update(self._semantic_trace_updates(decision))
                    return supported_query_updates
                recovered_updates = await self._maybe_recover_time_rescope_continuation(
                    trigger_reason="low_confidence_unclear",
                    decision=decision,
                    state=state,
                    session=session,
                    session_query_contract=session_query_contract,
                    message=message,
                    today=today,
                    language=locale,
                )
                if recovered_updates is not None:
                    self._log_single_item_followup(
                        surface=surface,
                        continuation_type="time_delta",
                        followup_outcome="time_rescope_query",
                        decision=decision.decision,
                    )
                    recovered_updates.update(self._semantic_trace_updates(decision))
                    return recovered_updates
                self._log_single_item_followup(
                    surface=surface,
                    continuation_type=cont_type,
                    followup_outcome="clarify",
                    decision=decision.decision,
                )
                return self._ambiguous_followup_updates(locale=locale, session=session)

        updates: dict[str, Any] = {
            "flow_state": "executing",
            "continuation_type": cont_type,
            "continuation_delta_type": decision.delta_type,
            "resolver_message": None,
            **self._semantic_trace_updates(decision),
        }

        if cont_type == "show_more":
            if original_query is None:
                return self._ambiguous_followup_updates(locale=locale, session=session)
            if followup_intent == "continue_pagination":
                if original_query.intent != QueryIntent.TRANSACTION_LIST:
                    return self._ambiguous_followup_updates(locale=locale, session=session)
                updates["current_page"] = session.get("current_page", 0) + 1
            elif followup_intent == "refine_existing":
                list_query = original_query.model_copy(deep=True)
                list_query.intent = QueryIntent.TRANSACTION_LIST
                list_query.aggregation = None
                updates["query_contract"] = QueryExecutionContract.from_normalized_query(
                    list_query,
                    continuation_type=cont_type,
                    continuation_delta_type=decision.delta_type,
                )
                updates["current_page"] = 0
                updates["show_expanded"] = False
            else:
                return self._ambiguous_followup_updates(locale=locale, session=session)

        elif cont_type == "time_delta":
            resolved_time_range, clarification_message = await self._resolve_time_delta_range(
                decision=decision,
                message=message,
                today=today,
                language=locale,
                state=state,
            )

            if original_query is None or resolved_time_range is None:
                if clarification_message:
                    return {
                        "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
                        "response": clarification_message,
                        "flow_state": "parsing",
                        "session_active": True,
                        "pending_clarification": None,
                        "show_expanded": bool(session.get("show_expanded", False)),
                        "current_page": session.get("current_page", 0),
                    }
                recovered_updates = await self._maybe_recover_time_rescope_continuation(
                    trigger_reason="missing_usable_delta",
                    decision=decision,
                    state=state,
                    session=session,
                    session_query_contract=session_query_contract,
                    message=message,
                    today=today,
                    language=locale,
                )
                if recovered_updates is not None:
                    self._log_single_item_followup(
                        surface=surface,
                        continuation_type="time_delta",
                        followup_outcome="time_rescope_query",
                        decision=decision.decision,
                    )
                    recovered_updates.update(self._semantic_trace_updates(decision))
                    return recovered_updates
                self._log_single_item_followup(
                    surface=surface,
                    continuation_type=cont_type,
                    followup_outcome="clarify",
                    decision=decision.decision,
                )
                return self._ambiguous_followup_updates(locale=locale, session=session)
            if followup_intent == "continue_pagination":
                return self._ambiguous_followup_updates(locale=locale, session=session)
            if followup_intent == "none":
                recovered_updates = await self._maybe_recover_time_rescope_continuation(
                    trigger_reason="missing_usable_delta",
                    decision=decision,
                    state=state,
                    session=session,
                    session_query_contract=session_query_contract,
                    message=message,
                    today=today,
                    language=locale,
                )
                if recovered_updates is not None:
                    self._log_single_item_followup(
                        surface=surface,
                        continuation_type="time_delta",
                        followup_outcome="time_rescope_query",
                        decision=decision.decision,
                    )
                    recovered_updates.update(self._semantic_trace_updates(decision))
                    return recovered_updates
                self._log_single_item_followup(
                    surface=surface,
                    continuation_type=cont_type,
                    followup_outcome="clarify",
                    decision=decision.decision,
                )
                return self._ambiguous_followup_updates(locale=locale, session=session)

            if followup_intent == "replace_scope":
                new_query = original_query.model_copy(deep=True)
                new_query.time_range = resolved_time_range
            else:
                new_query = apply_time_delta(original_query, resolved_time_range)

            if decision.result_limit is not None:
                new_query.result_limit = decision.result_limit
            if decision.result_reference is not None:
                new_query.result_reference = decision.result_reference
            updates["query_contract"] = QueryExecutionContract.from_normalized_query(
                new_query,
                continuation_type=cont_type,
                continuation_delta_type=decision.delta_type,
            )
            updates["current_page"] = 0
            updates["show_expanded"] = False
            self._log_single_item_followup(
                surface=surface,
                continuation_type=cont_type,
                followup_outcome="time_rescope_query",
                decision=decision.decision,
            )

        elif cont_type == "filter_delta":
            if original_query is None or followup_intent != "refine_existing":
                return self._ambiguous_followup_updates(locale=locale, session=session)

            delta_type = decision.delta_type
            allow_limit = delta_type in (None, "limit", "reference")
            allow_reference = delta_type in (None, "reference", "limit")

            new_query = apply_filter_delta(original_query, decision.filters) if decision.filters else original_query

            if decision.result_limit is not None and allow_limit:
                new_query.result_limit = decision.result_limit
            if decision.result_reference is not None and allow_reference:
                new_query.result_reference = decision.result_reference
            updates["query_contract"] = QueryExecutionContract.from_normalized_query(
                new_query,
                continuation_type=cont_type,
                continuation_delta_type=decision.delta_type,
            )
            updates["current_page"] = 0
            updates["show_expanded"] = False

        elif cont_type == "expand":
            if followup_intent != "refine_existing":
                return self._ambiguous_followup_updates(locale=locale, session=session)
            updates["show_expanded"] = True

        elif cont_type == "conversational":
            return self._append_query_session_transition(
                {
                    "transaction_outcome": TransactionOutcome.OK,
                    "response": self._compose_conversational_reply(
                        decision,
                        language=locale,
                    ),
                    "session_active": False,
                    "flow_state": "complete",
                    **self._semantic_trace_updates(decision),
                },
                "exit_query_session_conversational",
            )

        elif cont_type == "drill_down":
            drill_idx = decision.drill_down_index if decision.drill_down_index is not None else 0

            surface = session.get("surface")
            if surface and surface.type == SurfaceType.BREAKDOWN:
                if items and 0 <= drill_idx < len(items):
                    selected_item = items[drill_idx]
                    category_name = selected_item.description

                    from apps.core.src.agent.graphs.query.models import Filters

                    if original_query:
                        cat_filter = category_name.lower()

                        logger.info(
                            "breakdown_drill_down_debug",
                            original_description=category_name,
                            applied_filter=cat_filter,
                            item_index=drill_idx,
                        )

                        new_filters = Filters(category=[cat_filter])
                        new_query = apply_filter_delta(original_query, new_filters)
                        new_query.aggregation = None

                        updates["query_contract"] = QueryExecutionContract.from_normalized_query(
                            new_query,
                            continuation_type=cont_type,
                            continuation_delta_type=decision.delta_type,
                        )
                        updates["current_page"] = 0
                        updates["show_expanded"] = False

            elif items and 0 <= drill_idx < len(items):
                updates["selected_item_index"] = drill_idx
                updates["drill_down_action"] = decision.drill_down_action
                if decision.fact_field:
                    updates["fact_field"] = decision.fact_field
                if decision.drill_down_action == "answer_fact":
                    updates["_query_session_transition"] = "answer_fact_active_result"

        elif cont_type == "recipient_drill_down":
            recipient_name = decision.recipient_name
            if recipient_name and original_query:
                from apps.core.src.agent.graphs.query.models import Filters

                new_filters = Filters(merchant=[recipient_name])
                new_query = apply_filter_delta(original_query, new_filters)
                updates["query_contract"] = QueryExecutionContract.from_normalized_query(
                    new_query,
                    continuation_type=cont_type,
                    continuation_delta_type=decision.delta_type,
                )
                updates["current_page"] = 0
                updates["show_expanded"] = False

        elif cont_type == "unclear":
            supported_query_updates = await self._maybe_recover_supported_followup_query(
                state=state,
                today=today,
                language=locale,
                original_query=original_query,
                reasoner_extraction=getattr(decision, "extraction", None),
                reasoner_confidence=decision.confidence,
            )
            if supported_query_updates is not None:
                supported_query_updates.update(self._semantic_trace_updates(decision))
                return supported_query_updates
            recovered_updates = await self._maybe_recover_time_rescope_continuation(
                trigger_reason="unclear_continuation",
                decision=decision,
                state=state,
                session=session,
                session_query_contract=session_query_contract,
                message=message,
                today=today,
                language=locale,
            )
            if recovered_updates is not None:
                recovered_updates.update(self._semantic_trace_updates(decision))
                return recovered_updates
            return self._ambiguous_followup_updates(locale=locale, session=session)

        elif cont_type == "aggregate":
            aggregate_updates = await self._compile_aggregate_continuation_updates(
                decision=decision,
                state=state,
                today=today,
                language=locale,
                original_query=original_query,
            )
            if aggregate_updates is not None:
                return aggregate_updates
            return await self._parse_new_query(state)

        return updates

    async def _parse_new_query(self, state: dict[str, Any]) -> dict[str, Any]:
        """Parse a fresh query."""
        message = state.get("message", "")
        today_state = state.get("today")
        today = today_state if isinstance(today_state, date) else lagos_today()
        language = LocaleManager.normalize(state.get("language")).value

        decision = await self.reasoner.reason(
            SemanticReasonerContext(
                message=message,
                today=today,
                language=language,
                turn_id=state.get("turn_id"),
                inbound_message_id=state.get("inbound_message_id"),
            )
        )
        updates = await self._parse_reasoner_extraction_to_updates(
            decision,
            state=state,
            today=today,
            language=language,
        )
        updates.update(self._semantic_trace_updates(decision))
        return updates

    async def _parse_reasoner_extraction_to_updates(
        self,
        decision: Any,
        *,
        state: dict[str, Any],
        today: date,
        language: str,
    ) -> dict[str, Any]:
        """Translate semantic reasoner output into compiler-first query updates."""
        extraction = getattr(decision, "extraction", None)
        confidence = getattr(decision, "confidence", None)
        compiler_safe_extraction, compiler_safe_reason = self._compiler_safe_extraction_decision(
            extraction=extraction,
            confidence=confidence,
        )
        started_at = perf_counter()

        if extraction is not None:
            compile_target = extraction
            if not compile_target.raw_query:
                compile_target = compile_target.model_copy(update={"raw_query": state.get("message", "")})
            resolution_source = "reasoner_extraction_compile" if compiler_safe_extraction is not None else "reasoner_extraction_compile_with_ambiguity"
            if compiler_safe_extraction is None:
                logger.info(
                    "query_reasoner_parser_fallback",
                    reason=compiler_safe_reason,
                    semantic_decision=getattr(decision, "decision", None),
                    continuation_type=getattr(decision, "continuation_type", None),
                    confidence=confidence,
                    path="compile_extraction_despite_ambiguity",
                )
            result = self.parser.compile_extraction(compile_target, today=today, language=language)
            self._log_query_trace(
                state=state,
                phase="semantic_compile",
                latency_ms=(perf_counter() - started_at) * 1000.0,
                outcome=self._resolver_outcome_trace(getattr(result, "outcome", None)),
                resolution_source=resolution_source,
                semantic_decision=getattr(decision, "decision", None),
                continuation_type=getattr(decision, "continuation_type", None),
            )
            return self._parse_result_to_updates(result, state=state, today=today, language=language)

        logger.info(
            "query_reasoner_parser_fallback",
            reason=compiler_safe_reason,
            semantic_decision=getattr(decision, "decision", None),
            continuation_type=getattr(decision, "continuation_type", None),
            confidence=confidence,
        )
        result = await self.parser.parse(state.get("message", ""), today=today, language=language)
        self._log_query_trace(
            state=state,
            phase="semantic_compile",
            latency_ms=(perf_counter() - started_at) * 1000.0,
            outcome=self._resolver_outcome_trace(getattr(result, "outcome", None)),
            resolution_source="parser_parse",
            semantic_decision=getattr(decision, "decision", None),
            continuation_type=getattr(decision, "continuation_type", None),
        )
        return self._parse_result_to_updates(result, state=state, today=today, language=language)

    def _parse_result_to_updates(
        self,
        result: Any,
        *,
        state: dict[str, Any],
        today: date,
        language: str,
        message_override: str | None = None,
    ) -> dict[str, Any]:
        """Translate parser outcomes into extraction-step state updates."""

        if result.outcome == ResolverOutcome.NEEDS_INPUT:
            clarify_fallback = render_message("query.clarify.default", language)
            updates = {
                "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
                "response": result.resolver_message or clarify_fallback,
                "session_active": True,
                "pending_clarification": (
                    PendingClarificationState.model_validate(result.pending_clarification)
                    if result.pending_clarification
                    else None
                ),
                "flow_state": "parsing",
            }
            if result.resolver_message:
                updates["resolver_message"] = result.resolver_message
            return updates

        resolver_msg_parts = []
        if result.resolver_message and self._should_attach_resolver_message(result):
            resolver_msg_parts.append(result.resolver_message)

        if result.notices:
            resolver_msg_parts.extend(result.notices)

        resolver_msg = "\n".join(resolver_msg_parts) if resolver_msg_parts else None

        if result.extraction is None:
            return {
                "transaction_outcome": TransactionOutcome.FAILED,
                "response": render_message("query.error.general", language),
                "flow_state": "parsing",
            }

        query_contract = None
        if isinstance(result.query_contract, dict):
            try:
                query_contract = QueryExecutionContract.model_validate(result.query_contract)
            except Exception:
                query_contract = None

        if query_contract is None:
            query_ir = self.parser.build_query_ir_from_extraction(result.extraction, today=today, language=language)
            query_contract = self.parser.build_execution_contract_from_ir(query_ir)

        return {
            "query_contract": query_contract,
            "resolver_message": resolver_msg,
            "flow_state": "executing",
            "current_page": 0,
            "session_active": True,
            "pending_clarification": None,
            "show_expanded": False,
        }
