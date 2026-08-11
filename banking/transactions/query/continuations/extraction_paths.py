"""Active-session continuation handlers for the query extraction step."""

from __future__ import annotations

from datetime import date
from typing import Any

import banking.transactions.query.continuations.compiler_paths as compiler_paths
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome
from banking.transactions.query.continuations.active_result_facts import (
    maybe_build_fact_answer_from_decision,
)
from banking.transactions.query.continuations.grounded_followups import resolve_grounded_followup
from banking.transactions.query.continuations.repair_resolution import resolve_repair
from banking.transactions.query.continuations.result_paths import resolve_result_continuation_updates
from banking.transactions.query.continuations.supported_recovery import (
    maybe_recover_supported_followup_query,
)
from banking.transactions.query.continuations.time_rescope import (
    has_semantic_time_only_signal,
    is_direct_time_rescope_message,
    maybe_recover_time_rescope_continuation,
)
from banking.transactions.query.conversation_focus import resolve_focus, restore_focus
from banking.transactions.query.models.conversation import QueryTurnPlan
from banking.transactions.query.models.domain import (
    QueryIntent,
    QueryRequest,
    QueryResult,
    QueryResultItem,
)
from banking.transactions.query.models.extraction import ResolverOutcome
from banking.transactions.query.services.conversation.resolver import build_query_conversation_updates
from banking.transactions.query.utils.timezone import lagos_today
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_SHOW_EXISTING_TRANSACTIONS_MESSAGES = {
    "show them",
    "show me",
    "show transactions",
    "show the transactions",
    "show the transactions behind that",
    "show details",
    "show the details",
    "list them",
    "list transactions",
}

_REPEAT_EXISTING_QUERY_MESSAGES = {
    "check again",
    "check againo",
    "check it again",
    "recheck",
    "refresh",
    "run it again",
    "try again",
}


def _plan_step_request(raw_contract: object, step_id: str | None) -> QueryRequest | None:
    if not step_id:
        return None
    try:
        plan = raw_contract if isinstance(raw_contract, QueryTurnPlan) else QueryTurnPlan.model_validate(raw_contract)
    except Exception:
        return None
    step = next((candidate for candidate in plan.steps if candidate.step_id == step_id), None)
    return step.request if step is not None else None


def _normalize_show_existing_message(message: str) -> str:
    return " ".join(message.strip().split()).lower().rstrip("?.!,")


def _is_show_existing_transactions_followup(message: str, session_query_request: Any | None) -> bool:
    if session_query_request is None or session_query_request.intent not in {
        QueryIntent.ANALYTICS_SUMMARY,
        QueryIntent.BENEFICIARY_SUMMARY,
        QueryIntent.CASH_FLOW_SUMMARY,
        QueryIntent.TIME_COMPARISON,
    }:
        return False
    return _normalize_show_existing_message(message) in _SHOW_EXISTING_TRANSACTIONS_MESSAGES


def _is_repeat_existing_query_followup(message: str, session_query_request: Any | None) -> bool:
    if session_query_request is None:
        return False
    return _normalize_show_existing_message(message) in _REPEAT_EXISTING_QUERY_MESSAGES


def _repeat_existing_query_updates(
    step: Any,
    *,
    decision: Any,
    session_query_request: Any,
) -> dict[str, Any]:
    logger.info(
        "query_continuation_resolution",
        path="repeat_existing_query",
        semantic_decision=decision.decision,
        continuation_type=decision.continuation_type,
        followup_intent=decision.followup_intent,
    )

    return {
        "query_request": session_query_request,
        "resolver_message": None,
        "flow_state": "executing",
        "current_page": 0,
        "session_active": True,
        "pending_input": None,
        "show_expanded": False,
        "continuation_type": "repeat_query",
        "continuation_delta_type": None,
        **step._semantic_trace_updates(decision),
    }


def _deterministic_active_affordability_updates(
    step: Any,
    *,
    message: str,
    today: date,
    language: str,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """Promote a safe read-only affordability probe before semantic reasoning.

    This uses the existing typed deterministic query compiler; it does not
    infer a transfer or execute anything. It prevents an active transaction
    result from turning an explicit affordability question into a fact lookup.
    """
    parsed = step.parser.parse_deterministic(message, today=today, language=language)
    if parsed is None or parsed.outcome != ResolverOutcome.OK or not isinstance(parsed.query_request, dict):
        return None
    try:
        request = QueryRequest.model_validate(parsed.query_request)
    except Exception:
        return None
    if request.intent != QueryIntent.AFFORDABILITY:
        return None

    updates = compiler_paths.parse_result_to_updates(step, parsed, state=state, today=today, language=language)
    updates.update(
        {
            "_query_llm_calls_used": 0,
            "_query_single_llm_invariant": True,
        }
    )
    return step._append_query_session_transition(updates, "replace_session_new_query")


async def handle_continuation(step: Any, state: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    """Handle possible continuation of previous query."""
    message = state.get("message", "")
    today_state = state.get("today")
    today = today_state if isinstance(today_state, date) else lagos_today()
    session_query_request = step._load_session_query_request(session)
    locale = LocaleManager.normalize(state.get("language")).value
    query_frames = step._load_query_frames(session)
    # A visible evidence section is not a user decision.  Ground follow-ups
    # from the retained semantic focus when it points at a valid frame.
    focus = resolve_focus(frames=query_frames, active_focus=restore_focus(session.get("active_focus")))
    if focus is not None and focus.frame_id:
        focused_frame = next((frame for frame in query_frames if frame.frame_id == focus.frame_id), None)
        if focused_frame is not None:
            session_query_request = (
                _plan_step_request(focused_frame.execution_contract, focus.step_id) or focused_frame.query_request
            )
    if focus is not None:
        session_query_request = (
            _plan_step_request(session.get("execution_contract"), focus.step_id) or session_query_request
        )

    items: list[QueryResultItem] = []
    restored_query_result: QueryResult | None = None
    possible_result = session.get("display_result")

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
        has_query_request=session_query_request is not None,
        has_surface=bool(surface_view is not None),
        has_query_result=bool(session.get("display_result")),
        current_page=session.get("current_page", 0),
        show_expanded=bool(session.get("show_expanded", False)),
        surface_type=surface_view.mode.value if surface_view is not None else None,
    )
    deterministic_affordability = _deterministic_active_affordability_updates(
        step,
        message=message,
        today=today,
        language=locale,
        state=state,
    )
    if deterministic_affordability is not None:
        logger.info("query_continuation_resolution", path="deterministic_active_affordability")
        return deterministic_affordability

    decision = await step.reasoner.reason(
        step._build_reasoner_context(
            message=message,
            today=today,
            language=locale,
            state=state,
            query_request=session_query_request,
            items=items,
            surface_view=surface_view,
            query_frames=query_frames,
        )
    )
    target_step_id = getattr(decision, "target_step_id", None)
    session_query_request = (
        _plan_step_request(session.get("execution_contract"), target_step_id) or session_query_request
    )
    cont_type = decision.continuation_type or "unclear"
    # The focused-item schema has a dedicated typed recipient delta, but the
    # provider may still return the older generic drill-down label.  Normalize
    # that adapter result before any visible-target or fact-answer resolver can
    # consume the current item.  The recipient branch then rebuilds the source
    # fact request with the new counterparty deterministically.
    if cont_type == "drill_down" and str(getattr(decision, "recipient_name", None) or "").strip():
        cont_type = "recipient_drill_down"
        logger.info("query_recipient_continuation_normalized", source_type="drill_down")

    if state.get("recent_read_only") and getattr(decision, "drill_down_action", None) in {
        "re_transfer",
        "report_issue",
    }:
        logger.info("recent_query_context_recovery_rejected", reason="non_read_only_action")
        return {
            "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
            "response": render_message("query.clarify.recent_read_only", locale),
            "session_active": False,
            "flow_state": "complete",
        }

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

    if cont_type == "update_preferences" and getattr(decision, "preferences_update", None) is not None:
        logger.info(
            "query_preferences_handoff_created",
            changed_field_count=len(decision.preferences_update.model_fields_set),
            reset_all=decision.preferences_update.reset_all,
        )
        return {
            "transaction_outcome": TransactionOutcome.OK,
            "flow_state": "complete",
            "session_active": True,
            "query_preferences_handoff": decision.preferences_update.model_dump(
                mode="json",
                exclude_none=True,
                exclude_defaults=True,
            ),
            **step._semantic_trace_updates(decision),
        }

    if cont_type == "repair":
        repair_updates = resolve_repair(
            request=session_query_request,
            primary=getattr(decision, "repair_delta", None),
            alternate=getattr(decision, "alternate_repair_delta", None),
            confidence=decision.confidence,
            locale=locale,
            session=session,
            source_frame_id=(
                getattr(decision, "referenced_frame_ids", [None])[0]
                if getattr(decision, "referenced_frame_ids", None)
                else (focus.frame_id if focus is not None else None)
            ),
            turn_id=state.get("turn_id"),
            execution_contract=session.get("execution_contract"),
            target_step_id=target_step_id or (focus.step_id if focus is not None else None),
        )
        repair_updates.update(step._semantic_trace_updates(decision))
        return repair_updates

    if decision.decision == "continuation" and has_semantic_time_only_signal(decision):
        recovered_updates = await maybe_recover_time_rescope_continuation(
            step,
            trigger_reason="semantic_time_signal",
            decision=decision,
            state=state,
            session=session,
            session_query_request=session_query_request,
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

    if session_query_request is not None and step._is_spend_vs_earn_compare_followup(
        message=message,
        query_request=session_query_request,
    ):
        logger.info(
            "query_continuation_resolution",
            path="grounded_spend_vs_earn_comparison",
            semantic_decision=decision.decision,
        )
        return await resolve_result_continuation_updates(
            step,
            decision=decision,
            cont_type="grouped_total_followup",
            followup_intent="refine_existing",
            state=state,
            session=session,
            session_query_request=session_query_request,
            restored_query_result=restored_query_result,
            surface_view=surface_view,
            items=items,
            message=message,
            today=today,
            locale=locale,
        )

    defer_to_low_confidence_recovery = (
        decision.decision == "continuation"
        and cont_type == "unclear"
        and decision.confidence is not None
        and decision.confidence < step._LOW_CONFIDENCE_THRESHOLD
    )
    if is_direct_time_rescope_message(message, today=today) and not defer_to_low_confidence_recovery:
        recovered_updates = await maybe_recover_time_rescope_continuation(
            step,
            trigger_reason="semantic_direct_time_rescope",
            decision=decision,
            state=state,
            session=session,
            session_query_request=session_query_request,
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

    if _is_repeat_existing_query_followup(message, session_query_request):
        step._log_single_item_followup(
            surface_view=surface_view,
            continuation_type="repeat_query",
            followup_outcome="repeat_existing_query",
            decision=decision.decision,
        )
        return _repeat_existing_query_updates(
            step,
            decision=decision,
            session_query_request=session_query_request,
        )

    if _is_show_existing_transactions_followup(message, session_query_request):
        logger.info(
            "query_continuation_resolution",
            path="semantic_show_existing_transactions_recovery",
            semantic_decision=decision.decision,
            continuation_type=cont_type,
            followup_intent=decision.followup_intent,
        )
        step._log_single_item_followup(
            surface_view=surface_view,
            continuation_type="show_evidence",
            followup_outcome="show_existing_transactions",
            decision=decision.decision,
        )
        return await resolve_result_continuation_updates(
            step,
            decision=decision,
            cont_type="show_evidence",
            followup_intent="refine_existing",
            state=state,
            session=session,
            session_query_request=session_query_request,
            restored_query_result=restored_query_result,
            surface_view=surface_view,
            items=items,
            message=message,
            today=today,
            locale=locale,
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
        logger.info(
            "query_continuation_resolution", path="fallback_clarify_active_session", semantic_decision=decision.decision
        )
        return step._ambiguous_followup_updates(locale=locale, session=session)

    if cont_type == "coverage":
        return await resolve_result_continuation_updates(
            step,
            decision=decision,
            cont_type=cont_type,
            followup_intent=decision.followup_intent or "none",
            state=state,
            session=session,
            session_query_request=session_query_request,
            restored_query_result=restored_query_result,
            surface_view=surface_view,
            items=items,
            message=message,
            today=today,
            locale=locale,
        )

    # Reconciliation compares the current contract with a retained prior
    # frame. It must run before ordinary target grounding, which would
    # otherwise reinterpret the challenged label as a request to select it.
    if cont_type == "reconcile":
        return await resolve_result_continuation_updates(
            step,
            decision=decision,
            cont_type=cont_type,
            followup_intent=decision.followup_intent or "none",
            state=state,
            session=session,
            session_query_request=session_query_request,
            restored_query_result=restored_query_result,
            surface_view=surface_view,
            items=items,
            message=message,
            today=today,
            locale=locale,
        )

    conversation_updates = build_query_conversation_updates(
        surface_view=surface_view,
        query_result=restored_query_result,
        query_frames=query_frames,
        decision=decision,
        text=message,
        locale=locale,
        session=session,
        turn_id=state.get("turn_id"),
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
        session_query_request=session_query_request,
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

    # Aggregate continuations own their typed scope and must reach the
    # aggregate compiler. ``answer_mode=ask_clarify`` is advisory on a
    # reasoner response; allowing it to preempt an established aggregate
    # continuation turns a valid grouped follow-up into a generic rephrase.
    # A declared grounded operation is different: it is an explicit frame
    # request and therefore retains its existing higher-priority resolver.
    grounded_updates = (
        None
        if (cont_type in {"aggregate", "grouped_total_followup"} and not getattr(decision, "grounded_operation", None))
        else resolve_grounded_followup(
            step,
            decision=decision,
            session=session,
            language=locale,
        )
    )
    if grounded_updates is not None and step._should_ignore_grounded_query_for_aggregate(
        grounded_updates=grounded_updates,
        session_query_request=session_query_request,
        continuation_type=cont_type,
    ):
        logger.info(
            "query_grounded_followup_ignored",
            reason="aggregate_requires_new_query_shape",
            grounded_intent=grounded_updates["query_request"].intent.value,
            original_intent=session_query_request.intent.value if session_query_request is not None else None,
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
                session_query_request=session_query_request,
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
                has_original_scope=session_query_request is not None,
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
                session_query_request=session_query_request,
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
        session_query_request=session_query_request,
        restored_query_result=restored_query_result,
        surface_view=surface_view,
        items=items,
        message=message,
        today=today,
        locale=locale,
    )
