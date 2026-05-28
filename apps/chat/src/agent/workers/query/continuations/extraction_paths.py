"""Active-session continuation handlers for the query extraction step."""

from __future__ import annotations

from datetime import date
from typing import Any

import apps.chat.src.agent.workers.query.continuations.compiler_paths as compiler_paths
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome
from apps.chat.src.agent.workers.query.continuations.active_result_facts import (
    maybe_build_fact_answer_from_decision,
)
from apps.chat.src.agent.workers.query.continuations.grounded_followups import resolve_grounded_followup
from apps.chat.src.agent.workers.query.continuations.result_paths import resolve_result_continuation_updates
from apps.chat.src.agent.workers.query.continuations.supported_recovery import (
    maybe_recover_supported_followup_query,
)
from apps.chat.src.agent.workers.query.continuations.time_rescope import (
    maybe_recover_time_rescope_continuation,
)
from apps.chat.src.agent.workers.query.models.domain import (
    QueryResult,
    QueryResultItem,
)
from apps.chat.src.agent.workers.query.services.conversation.resolver import build_query_conversation_updates
from apps.chat.src.agent.workers.query.utils.timezone import lagos_today
from shared.i18n.locale import LocaleManager
from shared.utils.logging import get_logger

logger = get_logger(__name__)

async def handle_continuation(step: Any, state: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    """Handle possible continuation of previous query."""
    message = state.get("message", "")
    today_state = state.get("today")
    today = today_state if isinstance(today_state, date) else lagos_today()
    session_query_contract = step._load_session_query_contract(session)
    locale = LocaleManager.normalize(state.get("language")).value

    items: list[QueryResultItem] = []
    restored_query_result: QueryResult | None = None
    possible_result = session.get("query_result")

    if isinstance(possible_result, QueryResult):
        restored_query_result = possible_result
    elif isinstance(possible_result, dict):
        try:
            restored_query_result = QueryResult.model_validate(possible_result)
        except Exception:
            restored_query_result = None

    if restored_query_result is not None and restored_query_result.items:
        items = restored_query_result.items
    elif possible_result:
        raw_items = []
        if isinstance(possible_result, dict):
            raw_items = possible_result.get("items", [])
        else:
            raw_items = getattr(possible_result, "items", [])
        if raw_items:
            items = [QueryResultItem.model_validate(i) if isinstance(i, dict) else i for i in raw_items]

    surface_view = restored_query_result.surface_view if restored_query_result is not None else None
    logger.info(
        "query_continuation_entry",
        has_query_contract=session_query_contract is not None,
        has_surface=bool(surface_view is not None),
        has_query_result=bool(session.get("query_result")),
        current_page=session.get("current_page", 0),
        show_expanded=bool(session.get("show_expanded", False)),
        surface_type=surface_view.mode.value if surface_view is not None else None,
    )
    query_frames = step._load_query_frames(session)

    decision = await step.reasoner.reason(
        step._build_reasoner_context(
            message=message,
            today=today,
            language=locale,
            state=state,
            query_contract=session_query_contract,
            items=items,
            surface_view=surface_view,
            query_frames=query_frames,
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
        step._log_single_item_followup(
            surface_view=surface_view,
            continuation_type=cont_type,
            followup_outcome="end_session",
            decision=decision.decision,
        )
        return step._append_query_session_transition(
            {
                "transaction_outcome": TransactionOutcome.OK,
                "response": step._resolve_end_session_response(decision, locale=locale),
                "session_active": False,
                "flow_state": "complete",
                **step._semantic_trace_updates(decision),
            },
            "end_query_session",
        )

    if decision.decision in {"fresh_query", "new_query", "reinterpret_query"}:
        step._log_single_item_followup(
            surface_view=surface_view,
            continuation_type=cont_type,
            followup_outcome="reparse_query",
            decision=decision.decision,
        )
        logger.info("query_continuation_resolution", path="semantic_reparse", semantic_decision=decision.decision)
        semantic_updates = await compiler_paths.parse_reasoner_extraction_to_updates(
            step,
            decision,
            state=state,
            today=today,
            language=locale,
        )
        semantic_updates.update(step._semantic_trace_updates(decision))
        step._append_query_session_transition(semantic_updates, "replace_session_new_query")
        return semantic_updates

    if decision.decision != "continuation":
        step._log_single_item_followup(
            surface_view=surface_view,
            continuation_type=cont_type,
            followup_outcome="clarify",
            decision=decision.decision,
        )
        logger.info("query_continuation_resolution", path="fallback_clarify_active_session", semantic_decision=decision.decision)
        return step._ambiguous_followup_updates(locale=locale, session=session)

    conversation_updates = build_query_conversation_updates(
        surface_view=surface_view,
        query_result=restored_query_result,
        query_frames=query_frames,
        decision=decision,
        text=message,
    )
    if conversation_updates is not None:
        logger.info(
            "query_continuation_resolution",
            path="query_conversation_resolver",
            semantic_decision=decision.decision,
            continuation_type=cont_type,
            followup_intent=decision.followup_intent,
        )
        conversation_updates.update(step._semantic_trace_updates(decision))
        return conversation_updates

    semantic_fact_updates = maybe_build_fact_answer_from_decision(
        decision=decision,
        session=session,
        restored_query_result=restored_query_result,
        session_query_contract=session_query_contract,
        language=locale,
    )
    if semantic_fact_updates is not None:
        logger.info(
            "query_continuation_resolution",
            path="semantic_fact_answer",
            semantic_decision=decision.decision,
            continuation_type=cont_type,
            followup_intent=decision.followup_intent,
        )
        semantic_fact_updates.update(step._semantic_trace_updates(decision))
        return semantic_fact_updates

    grounded_updates = resolve_grounded_followup(
        step,
        decision=decision,
        session=session,
        language=locale,
    )
    if grounded_updates is not None and step._should_ignore_grounded_query_for_aggregate(
        grounded_updates=grounded_updates,
        session_query_contract=session_query_contract,
        continuation_type=cont_type,
    ):
        logger.info(
            "query_grounded_followup_ignored",
            reason="aggregate_requires_new_query_shape",
            grounded_intent=grounded_updates["query_contract"].intent.value,
            original_intent=session_query_contract.intent.value if session_query_contract is not None else None,
        )
        grounded_updates = None
    if grounded_updates is not None:
        if decision.answer_mode == "ask_clarify":
            recovered_updates = await maybe_recover_time_rescope_continuation(
                step,
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
                step._log_single_item_followup(
                    surface_view=surface_view,
                    continuation_type="time_delta",
                    followup_outcome="time_rescope_query",
                    decision=decision.decision,
                )
                recovered_updates.update(step._semantic_trace_updates(decision))
                return recovered_updates
        step._log_single_item_followup(
            surface_view=surface_view,
            continuation_type=cont_type,
            followup_outcome="clarify" if decision.answer_mode == "ask_clarify" else "grounded_answer",
            decision=decision.decision,
        )
        grounded_updates.update(step._semantic_trace_updates(decision))
        return grounded_updates

    followup_intent = decision.followup_intent or "none"
    if followup_intent not in (
        "refine_existing",
        "replace_scope",
        "continue_pagination",
        "previous_pagination",
        "none",
    ):
        followup_intent = "none"

    if decision.confidence is not None and decision.confidence < step._LOW_CONFIDENCE_THRESHOLD:
        if cont_type not in {"drill_down", "recipient_drill_down", "conversational"}:
            supported_query_updates = await maybe_recover_supported_followup_query(
                step,
                state=state,
                today=today,
                language=locale,
                has_original_scope=session_query_contract is not None,
                reasoner_extraction=getattr(decision, "extraction", None),
                reasoner_confidence=decision.confidence,
                parse_result_to_updates=compiler_paths.parse_result_to_updates,
            )
            if supported_query_updates is not None:
                supported_query_updates.update(step._semantic_trace_updates(decision))
                return supported_query_updates
            recovered_updates = await maybe_recover_time_rescope_continuation(
                step,
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
                step._log_single_item_followup(
                    surface_view=surface_view,
                    continuation_type="time_delta",
                    followup_outcome="time_rescope_query",
                    decision=decision.decision,
                )
                recovered_updates.update(step._semantic_trace_updates(decision))
                return recovered_updates
            step._log_single_item_followup(
                surface_view=surface_view,
                continuation_type=cont_type,
                followup_outcome="clarify",
                decision=decision.decision,
            )
            return step._ambiguous_followup_updates(locale=locale, session=session)

    return await resolve_result_continuation_updates(
        step,
        decision=decision,
        cont_type=cont_type,
        followup_intent=followup_intent,
        state=state,
        session=session,
        session_query_contract=session_query_contract,
        restored_query_result=restored_query_result,
        surface_view=surface_view,
        items=items,
        message=message,
        today=today,
        locale=locale,
    )
