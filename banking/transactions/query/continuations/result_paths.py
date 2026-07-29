"""Result-navigation continuation branches for active query sessions."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any, Literal, cast

import banking.transactions.query.continuations.compiler_paths as compiler_paths
from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome
from banking.transactions.query.continuations.aggregate_continuations import (
    compile_aggregate_continuation_updates,
)
from banking.transactions.query.continuations.aggregate_scope_reply import build_aggregate_scope_reply
from banking.transactions.query.continuations.scope_rescope import (
    maybe_recover_scope_broadening_continuation,
)
from banking.transactions.query.continuations.supported_recovery import maybe_recover_supported_followup_query
from banking.transactions.query.continuations.time_rescope import (
    maybe_recover_time_rescope_continuation,
    resolve_time_delta_range,
)
from banking.transactions.query.continuations.transforms import rebuild_query_request
from banking.transactions.query.contracts import SelectionPayload
from banking.transactions.query.models.domain import (
    Aggregation,
    Filters,
    QueryFactField,
    QueryIntent,
    QueryRequest,
    QueryResult,
    QueryResultItem,
)
from banking.transactions.query.models.operations import (
    AnalyzeOperation,
    CounterpartyConcentrationSpec,
)
from banking.transactions.query.presentation.selection_resolver import find_selection_payload
from banking.transactions.query.presentation.surface_builder import apply_selection_payload_to_query
from banking.transactions.query.services.answers.coverage import build_query_coverage_answer
from banking.transactions.query.services.conversation.targets import resolve_requested_fact_field
from banking.transactions.shared.correction_markers import (
    ASSERTIVE_CORRECTION_PREFIXES,
    has_assertive_correction_prefix,
    strip_correction_prefix,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


_PERSON_FRAME_RESIDUES: frozenset[str] = frozenset(
    {
        "who",
        "whom",
        "person",
        "recipient",
        "people",
        "persons",
        "someone",
        "somebody",
        "which person",
        "which recipient",
    }
)


def _is_counterparty_concentration_request(query_request: QueryRequest | None) -> bool:
    return bool(
        query_request
        and query_request.intent == QueryIntent.INSIGHT
        and isinstance(query_request.operation, AnalyzeOperation)
        and isinstance(query_request.operation.analysis, CounterpartyConcentrationSpec)
    )


def _is_person_frame_correction(message: str, target_text: str | None) -> bool:
    """Detect assertive corrections that retarget a counterparty answer to people."""
    if not has_assertive_correction_prefix(message):
        return False
    residue = strip_correction_prefix(message, prefixes=ASSERTIVE_CORRECTION_PREFIXES)
    cleaned_target = (target_text or "").lower().strip("?.!, ")
    if residue in _PERSON_FRAME_RESIDUES:
        return True
    if cleaned_target in _PERSON_FRAME_RESIDUES:
        return True
    return False


def _maybe_rebuild_intent_correction_request(
    message: str,
    target_text: str | None,
    session_query_request: QueryRequest | None,
) -> QueryRequest | None:
    """Rebuild a concentration answer into a beneficiary ranking when the user corrects intent."""
    if session_query_request is None:
        return None
    if not _is_person_frame_correction(message, target_text):
        return None
    if not _is_counterparty_concentration_request(session_query_request):
        return None
    return rebuild_query_request(
        session_query_request,
        intent=QueryIntent.BENEFICIARY_SUMMARY,
        filters=Filters(transaction_type="debit"),
        aggregation=Aggregation(type="count", group_by="merchant", limit=1),
        result_limit=1,
    )


def _transaction_direction_delta(decision: Any) -> Literal["credit", "debit", "both"] | None:
    explicit = getattr(decision, "transaction_direction_delta", None)
    if explicit in {"credit", "debit", "both"}:
        return cast(Literal["credit", "debit", "both"], explicit)
    return None


def _direction_refinement_contract(
    query_request: Any,
    direction: Literal["credit", "debit", "both"],
    *,
    continuation_delta_type: str | None,
) -> Any:
    filters = query_request.filters.model_copy(deep=True) if query_request.filters is not None else Filters()
    if direction == "both":
        filters.transaction_type = None
        return rebuild_query_request(
            query_request,
            filters=filters,
            merge_filters=False,
            intent=QueryIntent.ANALYTICS_SUMMARY,
            aggregation=Aggregation(type="breakdown", group_by="transaction_type"),
            result_limit=None,
            result_reference=None,
            answer_fact_field=None,
            continuation_type="aggregate",
            continuation_delta_type=continuation_delta_type,
            conversational_prefix=None,
        )
    filters.transaction_type = direction
    return rebuild_query_request(
        query_request,
        filters=filters,
        merge_filters=False,
        continuation_type="filter_delta",
        continuation_delta_type="filter",
        conversational_prefix=None,
    )


async def resolve_result_continuation_updates(
    step: Any,
    *,
    decision: Any,
    cont_type: str,
    followup_intent: str,
    state: dict[str, Any],
    session: dict[str, Any],
    session_query_request: Any | None,
    restored_query_result: QueryResult | None,
    surface_view: Any | None,
    items: list[QueryResultItem],
    message: str,
    today: date,
    locale: str,
) -> dict[str, Any]:
    """Resolve continuation branches that navigate or refine an existing query result."""
    continuation_delta_type = decision.delta_type or ("time" if cont_type == "time_delta" else None)
    updates: dict[str, Any] = {
        "flow_state": "executing",
        "continuation_type": cont_type,
        "continuation_delta_type": continuation_delta_type,
        "resolver_message": None,
        **step._semantic_trace_updates(decision),
    }

    direction_delta = _transaction_direction_delta(decision)
    if session_query_request is not None and direction_delta is not None:
        updates["continuation_type"] = "filter_delta" if direction_delta in {"credit", "debit"} else "aggregate"
        updates["continuation_delta_type"] = "filter"
        updates["query_request"] = _direction_refinement_contract(
            session_query_request,
            direction_delta,
            continuation_delta_type=continuation_delta_type,
        )
        updates["current_page"] = 0
        updates["show_expanded"] = False
        logger.info(
            "query_direction_refinement_normalized",
            direction=direction_delta,
            source="typed_semantic_decision",
        )
        return updates

    if session_query_request and step._is_income_vs_spending_followup(
        message=state.get("message", ""),
        query_request=session_query_request,
    ):
        new_filters = None
        if session_query_request.filters:
            new_filters = session_query_request.filters.model_copy()
            new_filters.transaction_type = None

        updates["query_request"] = rebuild_query_request(
            session_query_request,
            filters=new_filters,
            merge_filters=False,
            intent=QueryIntent.ANALYTICS_SUMMARY,
            aggregation=Aggregation(type="breakdown", group_by="transaction_type"),
            result_limit=None,
            result_reference=None,
            answer_fact_field=None,
            continuation_type="aggregate",
            continuation_delta_type=continuation_delta_type,
        )
        updates["current_page"] = 0
        updates["show_expanded"] = False
        return updates

    scope_recovery = await maybe_recover_scope_broadening_continuation(
        message=message,
        session_query_request=session_query_request,
    )
    if scope_recovery is not None:
        logger.info("query_scope_broadening_recovered")
        scope_recovery.update(step._semantic_trace_updates(decision))
        return scope_recovery

    if cont_type == "show_more":
        if session_query_request is None:
            return step._ambiguous_followup_updates(locale=locale, session=session)
        if followup_intent in {"continue_pagination", "previous_pagination"}:
            if session_query_request.intent not in {QueryIntent.TRANSACTION_LIST, QueryIntent.ANALYTICS_SUMMARY}:
                return step._ambiguous_followup_updates(locale=locale, session=session)
            current_page = int(session.get("current_page", 0) or 0)
            if followup_intent == "previous_pagination":
                if current_page <= 0:
                    return _pagination_boundary_updates(
                        locale=locale,
                        session=session,
                        message_key="query.pagination.already_first_page",
                    )
                updates["current_page"] = max(current_page - 1, 0)
            else:
                has_more = _result_has_more(restored_query_result=restored_query_result, session=session)
                if has_more is False:
                    return _pagination_boundary_updates(
                        locale=locale,
                        session=session,
                        message_key="query.pagination.end_of_results",
                    )
                updates["current_page"] = current_page + 1
        elif followup_intent == "refine_existing":
            selection_payload = None
            if surface_view is not None and len(surface_view.items) == 1:
                selection_payload = find_selection_payload(surface_view, index=0)

            if selection_payload is not None and (
                selection_payload.selection_kind in {"group_bucket", "summary_scope"}
                or bool(selection_payload.filters_patch)
                or selection_payload.time_patch is not None
            ):
                query_request = apply_selection_payload_to_query(
                    session_query_request,
                    selection_payload,
                )
                updates["query_request"] = query_request
                updates["conversational_prefix"] = decision.response_text
            else:
                updates["query_request"] = rebuild_query_request(
                    session_query_request,
                    intent=QueryIntent.TRANSACTION_LIST,
                    aggregation=None,
                    result_limit=None,
                    result_reference=None,
                    answer_fact_field=None,
                    continuation_type=cont_type,
                    continuation_delta_type=continuation_delta_type,
                    conversational_prefix=decision.response_text,
                )
            updates["current_page"] = 0
            updates["show_expanded"] = False
        else:
            return step._ambiguous_followup_updates(locale=locale, session=session)

    elif cont_type == "show_evidence":
        if session_query_request is None or session_query_request.intent not in {
            QueryIntent.ANALYTICS_SUMMARY,
            QueryIntent.CASH_FLOW_SUMMARY,
            QueryIntent.TIME_COMPARISON,
            QueryIntent.BENEFICIARY_SUMMARY,
            QueryIntent.INSIGHT,
        }:
            return step._ambiguous_followup_updates(locale=locale, session=session)

        selection_payload = find_selection_payload(surface_view, label=message)
        if selection_payload is None and surface_view is not None and len(surface_view.items) == 1:
            selection_payload = find_selection_payload(surface_view, index=0)

        if selection_payload is not None and (
            selection_payload.selection_kind in {"group_bucket", "summary_scope"}
            or bool(selection_payload.filters_patch)
            or selection_payload.time_patch is not None
        ):
            query_request = apply_selection_payload_to_query(
                session_query_request,
                selection_payload,
            )
            updates["query_request"] = query_request
            updates["conversational_prefix"] = None
        else:
            updates["query_request"] = rebuild_query_request(
                session_query_request,
                intent=QueryIntent.TRANSACTION_LIST,
                aggregation=None,
                result_limit=None,
                result_reference=None,
                answer_fact_field=None,
                continuation_type=cont_type,
                continuation_delta_type=continuation_delta_type,
                conversational_prefix=None,
            )
        updates["current_page"] = 0
        updates["show_expanded"] = False

    elif cont_type == "grouped_total_followup":
        if session_query_request is None:
            return step._ambiguous_followup_updates(locale=locale, session=session)
        if step._is_spend_vs_earn_compare_followup(message=message, query_request=session_query_request):
            # In-vs-out comparison on a grouped surface compiles to cash flow
            # over the same scope instead of dead-ending on a recipient total.
            updates["query_request"] = rebuild_query_request(
                session_query_request,
                intent=QueryIntent.CASH_FLOW_SUMMARY,
                filters=None,
                aggregation=None,
                result_limit=None,
                result_reference=None,
                answer_fact_field=None,
                continuation_type=cont_type,
                continuation_delta_type=continuation_delta_type,
            )
            updates["current_page"] = 0
            updates["show_expanded"] = False
        elif session_query_request.intent != QueryIntent.BENEFICIARY_SUMMARY:
            active_aggregation = session_query_request.aggregation
            if active_aggregation is None or active_aggregation.group_by != "account":
                return step._ambiguous_followup_updates(locale=locale, session=session)
            # The semantic contract has established that this is a grounded
            # grouped follow-up but did not provide a narrower typed target.
            # Re-run the authoritative grouped surface instead of treating a
            # bucket as a transaction or demanding an unnecessary rephrase.
            updates["query_request"] = rebuild_query_request(
                session_query_request,
                intent=QueryIntent.ANALYTICS_SUMMARY,
                aggregation=active_aggregation,
                result_limit=None,
                result_reference=None,
                answer_fact_field=None,
                continuation_type=cont_type,
                continuation_delta_type=continuation_delta_type,
            )
            updates["current_page"] = 0
            updates["show_expanded"] = False
        else:
            updates["query_request"] = rebuild_query_request(
                session_query_request,
                intent=QueryIntent.ANALYTICS_SUMMARY,
                aggregation=Aggregation(type="sum"),
                result_limit=None,
                result_reference=None,
                answer_fact_field=None,
                continuation_type=cont_type,
                continuation_delta_type=continuation_delta_type,
            )
            updates["current_page"] = 0
            updates["show_expanded"] = False

    elif cont_type == "time_delta":
        resolved_time_range, clarification_message = await resolve_time_delta_range(
            step,
            decision=decision,
            message=message,
            today=today,
            language=locale,
            state=state,
        )

        if session_query_request is None or resolved_time_range is None:
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
            recovered_updates = await maybe_recover_time_rescope_continuation(
                step,
                trigger_reason="missing_usable_delta",
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
        if followup_intent in {"continue_pagination", "previous_pagination"}:
            return step._ambiguous_followup_updates(locale=locale, session=session)
        if followup_intent == "none":
            recovered_updates = await maybe_recover_time_rescope_continuation(
                step,
                trigger_reason="missing_usable_delta",
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

        updates["query_request"] = rebuild_query_request(
            session_query_request,
            time_range=resolved_time_range,
            result_limit=decision.result_limit
            if decision.result_limit is not None
            else session_query_request.result_limit,
            result_reference=decision.result_reference
            if decision.result_reference is not None
            else session_query_request.result_reference,
            continuation_type=cont_type,
            continuation_delta_type=continuation_delta_type,
            conversational_prefix=decision.response_text,
        )
        updates["current_page"] = 0
        updates["show_expanded"] = False
        step._log_single_item_followup(
            surface_view=surface_view,
            continuation_type=cont_type,
            followup_outcome="time_rescope_query",
            decision=decision.decision,
        )

    elif cont_type == "filter_delta":
        if session_query_request is None:
            return step._ambiguous_followup_updates(locale=locale, session=session)
        if followup_intent not in {"refine_existing", "replace_scope"}:
            return step._ambiguous_followup_updates(locale=locale, session=session)

        delta_type = decision.delta_type
        allow_limit = delta_type in (None, "limit", "reference")
        allow_reference = delta_type in (None, "reference", "limit")
        replace_scope = followup_intent == "replace_scope"

        updates["query_request"] = rebuild_query_request(
            session_query_request,
            filters=decision.filters if decision.filters is not None else session_query_request.filters,
            merge_filters=decision.filters is not None and not replace_scope,
            result_limit=decision.result_limit
            if decision.result_limit is not None and allow_limit
            else session_query_request.result_limit,
            result_reference=decision.result_reference
            if decision.result_reference is not None and allow_reference
            else session_query_request.result_reference,
            continuation_type=cont_type,
            continuation_delta_type=continuation_delta_type,
            conversational_prefix=decision.response_text,
        )
        updates["current_page"] = 0
        updates["show_expanded"] = False

    elif cont_type == "expand":
        if followup_intent != "refine_existing":
            return step._ambiguous_followup_updates(locale=locale, session=session)
        updates["show_expanded"] = True

    elif cont_type == "conversational":
        return step._append_query_session_transition(
            {
                "transaction_outcome": TransactionOutcome.OK,
                "response": step._compose_conversational_reply(decision, language=locale),
                "session_active": False,
                "flow_state": "complete",
                "suppress_body_blocks": True,
                **step._semantic_trace_updates(decision),
            },
            "exit_query_session_conversational",
        )

    elif cont_type == "coverage":
        coverage_intent = _resolve_coverage_intent(decision, session_query_request)
        logger.info("query_coverage_intent_resolved", coverage_intent=coverage_intent)
        list_coverage_response = _build_result_list_coverage_response(
            restored_query_result=restored_query_result,
            session_query_request=session_query_request,
            session=session,
            locale=locale,
        )
        if coverage_intent == "result_completeness" and list_coverage_response is not None:
            return {
                "transaction_outcome": TransactionOutcome.OK,
                "response": list_coverage_response,
                "session_active": True,
                "flow_state": "complete",
                "suppress_body_blocks": True,
                "resolver_message": None,
                "show_expanded": bool(session.get("show_expanded", False)),
                "current_page": session.get("current_page", 0),
                **step._semantic_trace_updates(decision),
            }
        accounts_raw = state.get("accounts")
        accounts_info = (
            [account for account in accounts_raw if isinstance(account, dict)] if isinstance(accounts_raw, list) else []
        )
        response = await build_query_coverage_answer(
            accounts_info=accounts_info,
            query_request=session_query_request,
            session=session,
            target_text=getattr(decision, "target_text", None),
            locale=locale,
        )
        if coverage_intent == "ambiguous" and list_coverage_response is not None:
            response = f"{list_coverage_response}\n\n{response}"
        return {
            "transaction_outcome": TransactionOutcome.OK,
            "response": response,
            "session_active": True,
            "flow_state": "complete",
            "suppress_body_blocks": True,
            "resolver_message": None,
            "show_expanded": bool(session.get("show_expanded", False)),
            "current_page": session.get("current_page", 0),
            **step._semantic_trace_updates(decision),
        }

    elif cont_type == "explain_aggregate_scope":
        response_text = (getattr(decision, "response_text", None) or "").strip()
        contextual_hint = (getattr(decision, "contextual_hint", None) or "").strip()
        response = response_text or build_aggregate_scope_reply(
            session_query_request=session_query_request,
            query_result=restored_query_result,
            locale=locale,
        )
        if contextual_hint:
            response = f"{response}\n\n{contextual_hint}"
        return {
            "transaction_outcome": TransactionOutcome.OK,
            "response": response,
            "session_active": True,
            "flow_state": "complete",
            "suppress_body_blocks": True,
            "resolver_message": None,
            "show_expanded": bool(session.get("show_expanded", False)),
            "current_page": session.get("current_page", 0),
            **step._semantic_trace_updates(decision),
        }

    elif cont_type == "drill_down":
        raw_drill_idx = decision.drill_down_index
        drill_idx = raw_drill_idx if isinstance(raw_drill_idx, int) and raw_drill_idx >= 0 else None
        answer_fact_field = resolve_requested_fact_field(decision)
        drill_down_action = decision.drill_down_action or ("answer_fact" if answer_fact_field is not None else None)
        surface_context = getattr(surface_view, "context", {}) or {} if surface_view else {}
        if drill_idx is None and surface_view is not None:
            if surface_context.get("type") in {"single_transaction", "focused_transaction"}:
                selected_item_id = surface_context.get("selected_item_id")
                if selected_item_id:
                    for idx, item in enumerate(items):
                        if item.id == selected_item_id:
                            drill_idx = idx
                            break
                if drill_idx is None:
                    drill_idx = session.get("selected_item_index")
            elif len(getattr(surface_view, "items", []) or []) == 1:
                drill_idx = 0

        if drill_idx is None and _is_focused_aggregate_contract(session_query_request) and len(items) == 1:
            drill_idx = 0

        selection_payload = None
        if surface_view is not None and message:
            selection_payload = find_selection_payload(surface_view, label=message)
        if selection_payload is None and drill_idx is not None:
            selection_payload = find_selection_payload(surface_view, index=drill_idx)
        selection_payload = _normalize_focused_aggregate_selection_payload(
            selection_payload,
            session_query_request=session_query_request,
            surface_view=surface_view,
            items=items,
            drill_idx=drill_idx,
        )

        if (
            session_query_request is not None
            and selection_payload is not None
            and (
                selection_payload.selection_kind in {"group_bucket", "summary_scope"}
                or bool(selection_payload.filters_patch)
                or selection_payload.time_patch is not None
            )
        ):
            query_fact_field: QueryFactField | None = None
            if drill_down_action == "answer_fact" and answer_fact_field is not None:
                query_fact_field = "counterparty" if answer_fact_field == "recipient" else answer_fact_field
            updates["query_request"] = apply_selection_payload_to_query(
                session_query_request,
                selection_payload,
                fact_field=query_fact_field,
            )
            updates["current_page"] = 0
            updates["show_expanded"] = False
            return step._append_query_session_transition(updates, "replace_session_new_query")

        if drill_idx is not None and items and 0 <= drill_idx < len(items):
            updates["selected_item_index"] = drill_idx
            if selection_payload is not None:
                updates["selected_payload"] = selection_payload
            updates["drill_down_action"] = drill_down_action
            if answer_fact_field:
                updates["fact_field"] = "recipient" if answer_fact_field == "counterparty" else answer_fact_field
            if drill_down_action == "answer_fact":
                updates["_query_session_transition"] = "answer_fact_active_result"
        else:
            return _unresolved_selection_updates(locale=locale, session=session, visible_count=len(items))

    elif cont_type == "recipient_drill_down":
        recipient_name = decision.recipient_name
        if recipient_name and session_query_request is not None:
            recipient_answer_fact_field: QueryFactField | None = None
            if decision.fact_field in {
                "date",
                "amount",
                "bank",
                "status",
                "description",
                "reference",
                "account",
                "direction",
                "category",
            }:
                recipient_answer_fact_field = cast(QueryFactField, decision.fact_field)
            selection_payload = find_selection_payload(surface_view, label=recipient_name)
            if selection_payload is not None:
                updates["query_request"] = apply_selection_payload_to_query(
                    session_query_request,
                    selection_payload,
                    fact_field=recipient_answer_fact_field,
                )
            else:
                new_filters = Filters(counterparty=[recipient_name])
                updates["query_request"] = rebuild_query_request(
                    session_query_request,
                    filters=new_filters,
                    merge_filters=True,
                    intent=QueryIntent.TRANSACTION_LIST,
                    aggregation=None,
                    result_limit=None,
                    result_reference=None,
                    answer_fact_field=recipient_answer_fact_field,
                    continuation_type=cont_type,
                    continuation_delta_type=decision.delta_type,
                )
            updates["current_page"] = 0
            updates["show_expanded"] = False
        else:
            return _unresolved_selection_updates(locale=locale, session=session, visible_count=len(items))

    elif cont_type == "unclear":
        if decision.reason == "query_reasoner_timeout" and decision.response_text:
            return {
                "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
                "response": decision.response_text,
                "flow_state": "parsing",
                "session_active": True,
                "pending_clarification": session.get("pending_clarification"),
                "show_expanded": bool(session.get("show_expanded", False)),
                "current_page": session.get("current_page", 0),
            }
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
            trigger_reason="unclear_continuation",
            decision=decision,
            state=state,
            session=session,
            session_query_request=session_query_request,
            message=message,
            today=today,
            language=locale,
        )
        if recovered_updates is not None:
            recovered_updates.update(step._semantic_trace_updates(decision))
            return recovered_updates
        intent_corrected_request = _maybe_rebuild_intent_correction_request(
            message,
            getattr(decision, "target_text", None),
            session_query_request,
        )
        if intent_corrected_request is not None:
            logger.info(
                "query_intent_correction_recovered",
                source="unclear",
                from_intent="counterparty_concentration",
                to_intent="beneficiary_summary",
            )
            return {
                "flow_state": "executing",
                "continuation_type": "unclear",
                "continuation_delta_type": "intent_correction",
                "resolver_message": None,
                "query_request": intent_corrected_request,
                "current_page": 0,
                "show_expanded": False,
                "session_active": True,
                **step._semantic_trace_updates(decision),
            }
        return step._ambiguous_followup_updates(locale=locale, session=session)

    elif cont_type == "recheck":
        if session_query_request is None:
            return step._ambiguous_followup_updates(locale=locale, session=session)
        updates["query_request"] = rebuild_query_request(
            session_query_request,
            continuation_type=cont_type,
            continuation_delta_type=continuation_delta_type,
            conversational_prefix=decision.response_text,
        )
        updates["current_page"] = 0
        updates["show_expanded"] = False

    elif cont_type == "reconcile":
        from banking.transactions.query.continuations.reconciliation import reconcile_query_answer

        corrected_request = _maybe_rebuild_intent_correction_request(
            message,
            getattr(decision, "target_text", None),
            session_query_request,
        )
        if corrected_request is not None:
            logger.info(
                "query_intent_correction_recovered",
                source="reconcile",
                from_intent="counterparty_concentration",
                to_intent="beneficiary_summary",
            )
            return {
                "flow_state": "executing",
                "continuation_type": "reconcile",
                "continuation_delta_type": "intent_correction",
                "resolver_message": None,
                "query_request": corrected_request,
                "current_page": 0,
                "show_expanded": False,
                "session_active": True,
                **step._semantic_trace_updates(decision),
            }

        reconciliation = await reconcile_query_answer(
            session_query_request=session_query_request,
            # Frames are persisted as JSON in the checkpoint.  Rehydrate them
            # through the same trusted boundary used by the reasoner before
            # reconciliation reads visible_items or replays an evidence
            # selector.
            query_frames=step._load_query_frames(session),
            target_text=getattr(decision, "target_text", None),
            target_amount=getattr(decision, "target_amount", None),
            referenced_frame_ids=getattr(decision, "referenced_frame_ids", None),
            locale=locale,
        )
        logger.info(
            "query_reconciliation",
            outcome=reconciliation.outcome,
            source_frame_id=reconciliation.source_frame_id,
            difference_categories=list(reconciliation.difference_categories),
            has_evidence=reconciliation.evidence_payload is not None,
        )
        if reconciliation.outcome == "evidence_replay":
            source_request = reconciliation.source_query_request
            payload = reconciliation.evidence_payload
            if source_request is None or payload is None:
                logger.info("query_reconciliation_replay_rejected", reason="missing_source_contract")
            else:
                try:
                    updates.update(
                        {
                            "query_request": apply_selection_payload_to_query(source_request, payload),
                            "conversational_prefix": reconciliation.response,
                            "current_page": 0,
                            "show_expanded": False,
                            "selected_frame_id": reconciliation.source_frame_id,
                            "continuation_type": "show_evidence",
                        }
                    )
                    logger.info(
                        "query_reconciliation_evidence_replayed",
                        source_frame_id=reconciliation.source_frame_id,
                    )
                    return step._append_query_session_transition(updates, "replace_session_new_query")
                except (TypeError, ValueError):
                    logger.info("query_reconciliation_replay_rejected", reason="invalid_evidence_contract")
        return {
            "transaction_outcome": TransactionOutcome.OK,
            "response": reconciliation.response,
            "session_active": True,
            "flow_state": "complete",
            "suppress_body_blocks": True,
            "resolver_message": None,
            "show_expanded": bool(session.get("show_expanded", False)),
            "current_page": int(session.get("current_page", 0) or 0),
            **step._semantic_trace_updates(decision),
        }

    elif cont_type == "aggregate":
        aggregate_updates = await compile_aggregate_continuation_updates(
            step,
            decision=decision,
            state=state,
            today=today,
            language=locale,
            session_query_request=session_query_request,
            parse_result_to_updates=compiler_paths.parse_result_to_updates,
            parse_reasoner_extraction_to_updates=compiler_paths.parse_reasoner_extraction_to_updates,
        )
        if aggregate_updates is not None:
            return aggregate_updates
        return step._ambiguous_followup_updates(locale=locale, session=session)

    if "query_request" in updates and updates.get("query_request") is not session_query_request:
        return step._append_query_session_transition(updates, "replace_session_new_query")

    return updates


CoverageIntent = Literal["result_completeness", "data_coverage", "ambiguous"]


def _resolve_coverage_intent(decision: Any, query_request: Any | None) -> CoverageIntent:
    semantic_intent = getattr(decision, "coverage_intent", None)
    if semantic_intent in {"result_completeness", "data_coverage", "ambiguous"}:
        return cast(CoverageIntent, semantic_intent)
    # Backward-compatible structural default for older reasoner payloads. An
    # active transaction list owns questions about whether matching rows remain;
    # non-list results cannot safely infer synchronization coverage.
    if query_request is not None and query_request.intent == QueryIntent.TRANSACTION_LIST:
        return "result_completeness"
    return "ambiguous"


def _pagination_boundary_updates(*, locale: str, session: dict[str, Any], message_key: MessageKey) -> dict[str, Any]:
    return {
        "transaction_outcome": TransactionOutcome.OK,
        "response": render_message(message_key, locale),
        "session_active": True,
        "flow_state": "complete",
        "resolver_message": None,
        "show_expanded": bool(session.get("show_expanded", False)),
        "current_page": int(session.get("current_page", 0) or 0),
    }


def _result_has_more(*, restored_query_result: QueryResult | None, session: dict[str, Any]) -> bool | None:
    if restored_query_result is not None:
        return restored_query_result.has_more
    raw_result = session.get("query_result")
    if isinstance(raw_result, dict) and isinstance(raw_result.get("has_more"), bool):
        return raw_result["has_more"]
    return None


def _unresolved_selection_updates(*, locale: str, session: dict[str, Any], visible_count: int) -> dict[str, Any]:
    message_key: MessageKey = (
        "query.drill_down.no_items" if visible_count <= 0 else "query.drill_down.invalid_selection"
    )
    return {
        "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
        "response": render_message(message_key, locale, {"count": visible_count}),
        "session_active": True,
        "flow_state": "parsing",
        "pending_clarification": None,
        "resolver_message": None,
        "selected_item_index": None,
        "selected_item_id": None,
        "selected_payload": None,
        "selected_query_item": None,
        "selected_frame_id": None,
        "drill_down_action": None,
        "fact_field": None,
        "show_expanded": bool(session.get("show_expanded", False)),
        "current_page": int(session.get("current_page", 0) or 0),
    }


def _build_result_list_coverage_response(
    *,
    restored_query_result: QueryResult | None,
    session_query_request: Any | None,
    session: dict[str, Any],
    locale: str,
) -> str | None:
    if restored_query_result is None:
        return None
    query_request = restored_query_result.query_request or session_query_request
    if query_request is None or query_request.intent != QueryIntent.TRANSACTION_LIST:
        return None
    surface_context = (
        restored_query_result.surface_view.context
        if restored_query_result.surface_view is not None
        and isinstance(restored_query_result.surface_view.context, dict)
        else {}
    )
    parent_visible_count = surface_context.get("parent_visible_count")
    visible_count = (
        parent_visible_count
        if isinstance(parent_visible_count, int) and parent_visible_count > 0
        else len(restored_query_result.items or [])
    )
    current_page = int(session.get("current_page", 0) or 0)
    page_size = int(session.get("page_size", 5) or 5)
    shown_count = max(visible_count, (current_page * page_size) + visible_count)
    if restored_query_result.has_more:
        return render_message(
            "query.coverage_copy.result_more",
            locale,
            {"count": shown_count},
        )
    if visible_count:
        return render_message("query.coverage_copy.result_complete", locale, {"count": shown_count})
    return render_message("query.coverage_copy.result_empty", locale)


def _normalize_focused_aggregate_selection_payload(
    selection_payload: SelectionPayload | None,
    *,
    session_query_request: Any | None,
    surface_view: Any | None,
    items: list[QueryResultItem],
    drill_idx: int | None,
) -> SelectionPayload | None:
    """Repair stale focused aggregate payloads into scoped query payloads.

    Older persisted query sessions may have a focused beneficiary surface whose
    item payload still looks like a concrete transaction. The active contract
    and focused surface are enough to preserve the semantic selection without
    reading rendered text.
    """
    if (
        session_query_request is None
        or not _is_focused_aggregate_contract(session_query_request)
        or surface_view is None
        or drill_idx is None
    ):
        return selection_payload
    if selection_payload is not None and (
        bool(selection_payload.filters_patch) or selection_payload.time_patch is not None
    ):
        return selection_payload

    surface_items = getattr(surface_view, "items", None) or []
    if len(surface_items) != 1 or not (0 <= drill_idx < len(surface_items)):
        return _focused_aggregate_selection_payload_from_result_item(
            selection_payload,
            session_query_request=session_query_request,
            items=items,
            drill_idx=drill_idx,
        )

    surface_item = surface_items[drill_idx]
    return _focused_aggregate_selection_payload_from_surface_item(
        selection_payload,
        session_query_request=session_query_request,
        surface_item=surface_item,
    )


def _is_focused_aggregate_contract(session_query_request: Any | None) -> bool:
    if session_query_request is None:
        return False
    intent = getattr(session_query_request, "intent", None)
    if intent == QueryIntent.BENEFICIARY_SUMMARY:
        return True
    if intent != QueryIntent.ANALYTICS_SUMMARY:
        return False
    aggregation = getattr(session_query_request, "aggregation", None)
    return bool(getattr(aggregation, "group_by", None))


def _focused_aggregate_selection_payload_from_result_item(
    selection_payload: SelectionPayload | None,
    *,
    session_query_request: Any,
    items: list[QueryResultItem],
    drill_idx: int,
) -> SelectionPayload | None:
    if not (0 <= drill_idx < len(items)):
        return selection_payload

    item = items[drill_idx]
    label = (
        str(getattr(selection_payload, "label", "") if selection_payload is not None else "").strip()
        or str(getattr(item, "description", "") or "").strip()
    )
    if not label:
        return selection_payload

    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    synthetic_surface_item = SimpleNamespace(
        id=item.id,
        label=label,
        description=item.description,
        date=item.date,
        metadata=metadata,
    )
    return _focused_aggregate_selection_payload_from_surface_item(
        selection_payload,
        session_query_request=session_query_request,
        surface_item=synthetic_surface_item,
    )


def _focused_aggregate_selection_payload_from_surface_item(
    selection_payload: SelectionPayload | None,
    *,
    session_query_request: Any,
    surface_item: Any,
) -> SelectionPayload | None:
    intent = getattr(session_query_request, "intent", None)
    label = (
        str(getattr(selection_payload, "label", "") if selection_payload is not None else "").strip()
        or str(getattr(surface_item, "label", "") or "").strip()
    )
    if not label:
        return selection_payload

    filters_patch: dict[str, Any] = {}
    time_patch: dict[str, Any] | None = None
    selection_kind = "group_bucket"
    entity_type = "group_bucket"
    group_by = None
    group_key = label

    if intent == QueryIntent.BENEFICIARY_SUMMARY:
        selection_kind = "beneficiary"
        entity_type = "beneficiary"
        filters_patch = {"counterparty": [label]}
    else:
        aggregation = getattr(session_query_request, "aggregation", None)
        group_by = getattr(aggregation, "group_by", None) if aggregation else None
        if not group_by:
            return selection_payload

        metadata = getattr(surface_item, "metadata", {}) or {}
        group_key = str(metadata.get("key") or getattr(surface_item, "description", "") or label).strip()

        if group_by == "account":
            filters_patch["account_filter"] = group_key
        elif group_by == "merchant":
            filters_patch["counterparty"] = [group_key]
        elif group_by == "transaction_type":
            tx_type = group_key.lower()
            if tx_type in {"credit", "debit"}:
                filters_patch["transaction_type"] = tx_type
        elif group_by == "day":
            date_val = getattr(surface_item, "date", None)
            if date_val:
                time_patch = {"start": date_val.isoformat(), "end": date_val.isoformat(), "granularity": "day"}
        else:
            filters_patch["category"] = [group_key.lower()]

    if selection_payload is None:
        return SelectionPayload(
            selection_kind=cast(Any, selection_kind),
            entity_type=entity_type,
            entity_id=str(getattr(surface_item, "id", "") or "") or None,
            label=label,
            group_by=group_by,
            group_key=group_key,
            filters_patch=filters_patch,
            time_patch=time_patch,
        )

    return selection_payload.model_copy(
        update={
            "selection_kind": selection_kind,
            "entity_type": entity_type,
            "label": label,
            "group_by": group_by,
            "group_key": group_key,
            "filters_patch": filters_patch,
            "time_patch": time_patch,
        }
    )


__all__ = ["resolve_result_continuation_updates"]
