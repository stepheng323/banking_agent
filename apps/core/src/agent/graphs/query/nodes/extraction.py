"""Extraction step for query pipeline."""

from datetime import date
from typing import Any

from langchain_core.runnables import Runnable

from apps.core.src.agent.graphs.query.models import (
    AmbiguityCode,
    PendingClarificationState,
    QueryExecutionContract,
    QueryIntent,
    QueryResultItem,
    ResolverOutcome,
    ResultSurface,
    SurfaceType,
)
from apps.core.src.agent.graphs.query.pipeline import QueryStep
from apps.core.src.agent.graphs.query.services.continuity import (
    apply_filter_delta,
    apply_time_delta,
)
from apps.core.src.agent.graphs.query.services.parser import QueryParser
from apps.core.src.agent.graphs.query.services.reasoner import QuerySemanticReasoner, SemanticReasonerContext
from apps.core.src.agent.graphs.query.utils.timezone import lagos_today
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.i18n import LocaleManager, render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ExtractionStep(QueryStep):
    """Extracts intent and parameters for query."""

    _LOW_CONFIDENCE_THRESHOLD = 0.45

    def __init__(self, llm: Runnable):
        self.parser = QueryParser(llm)
        self.reasoner = QuerySemanticReasoner(llm)

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
                "response": render_message("query.session.goodbye", locale),
                "session_active": False,
                "pending_clarification": None,
                "flow_state": "complete",
                **self._semantic_trace_updates(decision),
            }, "end_query_session")

        if decision.decision == "new_query":
            return self._parse_reasoner_extraction_to_updates(
                decision,
                state=state,
                today=today,
                language=locale,
            )

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
            result = self.parser.resolve_existing_extraction(
                patched_extraction,
                today=today,
                language=locale,
            )
            updates = self._parse_result_to_updates(result, state=state, today=today, language=locale)
            updates.update(self._semantic_trace_updates(decision))
            return updates

        updates = self._parse_reasoner_extraction_to_updates(
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
        locale = LocaleManager.normalize(state.get("language")).value

        decision = await self.reasoner.reason(
            SemanticReasonerContext(
                message=message,
                today=today,
                language=locale,
                query_contract=session_query_contract,
                items=items,
                surface=surface,
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

        if decision.decision == "end_session":
            return self._append_query_session_transition({
                "transaction_outcome": TransactionOutcome.OK,
                "response": decision.end_session_response or render_message("query.session.goodbye", locale),
                "session_active": False,
                "flow_state": "complete",
                **self._semantic_trace_updates(decision),
            }, "end_query_session")

        if decision.decision in {"fresh_query", "new_query", "reinterpret_query"}:
            semantic_updates = self._parse_reasoner_extraction_to_updates(
                decision,
                state=state,
                today=today,
                language=locale,
            )
            semantic_updates.update(self._semantic_trace_updates(decision))
            self._append_query_session_transition(semantic_updates, "replace_session_new_query")
            return semantic_updates

        if decision.decision != "continuation":
            return await self._parse_new_query(state)

        followup_intent = decision.followup_intent or "none"
        if followup_intent not in ("refine_existing", "replace_scope", "continue_pagination", "none"):
            followup_intent = "none"

        if decision.confidence is not None and decision.confidence < self._LOW_CONFIDENCE_THRESHOLD:
            if cont_type not in {"drill_down", "recipient_drill_down", "conversational"}:
                return self._ambiguous_followup_updates(locale=locale, session=session)

        updates: dict[str, Any] = {
            "flow_state": "executing",
            "continuation_type": cont_type,
            "continuation_delta_type": decision.delta_type,
            "resolver_message": None,
            **self._semantic_trace_updates(decision),
        }
        original_query = session_query_contract.normalized_query if session_query_contract else None

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
            if original_query is None or decision.time_range is None:
                return self._ambiguous_followup_updates(locale=locale, session=session)
            if followup_intent == "continue_pagination" or followup_intent == "none":
                return self._ambiguous_followup_updates(locale=locale, session=session)

            if followup_intent == "replace_scope":
                new_query = original_query.model_copy(deep=True)
                new_query.time_range = decision.time_range
            else:
                new_query = apply_time_delta(original_query, decision.time_range)

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
            return self._ambiguous_followup_updates(locale=locale, session=session)

        elif cont_type == "aggregate":
            if decision.extraction is not None:
                return self._parse_reasoner_extraction_to_updates(
                    decision,
                    state=state,
                    today=today,
                    language=locale,
                )
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
            )
        )
        updates = self._parse_reasoner_extraction_to_updates(decision, state=state, today=today, language=language)
        updates.update(self._semantic_trace_updates(decision))
        return updates

    def _parse_reasoner_extraction_to_updates(
        self,
        decision: Any,
        *,
        state: dict[str, Any],
        today: date,
        language: str,
    ) -> dict[str, Any]:
        """Translate semantic reasoner extraction output into parser/compiler updates."""
        extraction = getattr(decision, "extraction", None)
        if extraction is None:
            return {
                "transaction_outcome": TransactionOutcome.FAILED,
                "response": render_message("query.error.general", language),
                "flow_state": "parsing",
            }
        if not extraction.raw_query:
            extraction = extraction.model_copy(update={"raw_query": state.get("message", "")})
        result = self.parser.resolve_existing_extraction(extraction, today=today, language=language)
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
        if result.resolver_message:
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
