"""Result-navigation continuation branches for active query sessions."""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import banking.transactions.query.continuations.compiler_paths as compiler_paths
from banking.runtime.results import TransactionOutcome
from banking.transactions.query.continuations.aggregate_continuations import (
    compile_aggregate_continuation_updates,
)
from banking.transactions.query.continuations.aggregate_scope_reply import build_aggregate_scope_reply
from banking.transactions.query.continuations.supported_recovery import maybe_recover_supported_followup_query
from banking.transactions.query.continuations.time_rescope import (
    maybe_recover_time_rescope_continuation,
    resolve_time_delta_range,
)
from banking.transactions.query.continuations.transforms import rebuild_query_contract
from banking.transactions.query.contracts import SelectionPayload
from banking.transactions.query.models.domain import (
    Aggregation,
    Filters,
    QueryFactField,
    QueryIntent,
    QueryResult,
    QueryResultItem,
)
from banking.transactions.query.presentation.selection_resolver import find_selection_payload
from banking.transactions.query.presentation.surface_builder import apply_selection_payload_to_query
from banking.transactions.query.services.answers.coverage import build_query_coverage_answer
from banking.transactions.query.services.conversation.targets import resolve_requested_fact_field


async def resolve_result_continuation_updates(
    step: Any,
    *,
    decision: Any,
    cont_type: str,
    followup_intent: str,
    state: dict[str, Any],
    session: dict[str, Any],
    session_query_contract: Any | None,
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

    if cont_type == "show_more":
        if session_query_contract is None:
            return step._ambiguous_followup_updates(locale=locale, session=session)
        if followup_intent in {"continue_pagination", "previous_pagination"}:
            if session_query_contract.intent not in {QueryIntent.TRANSACTION_LIST, QueryIntent.ANALYTICS_SUMMARY}:
                return step._ambiguous_followup_updates(locale=locale, session=session)
            current_page = int(session.get("current_page", 0) or 0)
            if followup_intent == "previous_pagination":
                updates["current_page"] = max(current_page - 1, 0)
            else:
                updates["current_page"] = current_page + 1
        elif followup_intent == "refine_existing":
            updates["query_contract"] = rebuild_query_contract(
                session_query_contract,
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
        if session_query_contract is None or session_query_contract.intent != QueryIntent.ANALYTICS_SUMMARY:
            return step._ambiguous_followup_updates(locale=locale, session=session)
        updates["query_contract"] = rebuild_query_contract(
            session_query_contract,
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

    elif cont_type == "grouped_total_followup":
        if session_query_contract is None or session_query_contract.intent != QueryIntent.BENEFICIARY_SUMMARY:
            return step._ambiguous_followup_updates(locale=locale, session=session)
        updates["query_contract"] = rebuild_query_contract(
            session_query_contract,
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

        if session_query_contract is None or resolved_time_range is None:
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
        if followup_intent in {"continue_pagination", "previous_pagination"}:
            return step._ambiguous_followup_updates(locale=locale, session=session)
        if followup_intent == "none":
            recovered_updates = await maybe_recover_time_rescope_continuation(
                step,
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

        updates["query_contract"] = rebuild_query_contract(
            session_query_contract,
            time_range=resolved_time_range,
            result_limit=decision.result_limit
            if decision.result_limit is not None
            else session_query_contract.result_limit,
            result_reference=decision.result_reference
            if decision.result_reference is not None
            else session_query_contract.result_reference,
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
        if followup_intent != "refine_existing" or session_query_contract is None:
            return step._ambiguous_followup_updates(locale=locale, session=session)

        delta_type = decision.delta_type
        allow_limit = delta_type in (None, "limit", "reference")
        allow_reference = delta_type in (None, "reference", "limit")

        updates["query_contract"] = rebuild_query_contract(
            session_query_contract,
            filters=decision.filters if decision.filters is not None else session_query_contract.filters,
            merge_filters=decision.filters is not None,
            result_limit=decision.result_limit
            if decision.result_limit is not None and allow_limit
            else session_query_contract.result_limit,
            result_reference=decision.result_reference
            if decision.result_reference is not None and allow_reference
            else session_query_contract.result_reference,
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
                **step._semantic_trace_updates(decision),
            },
            "exit_query_session_conversational",
        )

    elif cont_type == "coverage":
        accounts_raw = state.get("accounts")
        accounts_info = (
            [account for account in accounts_raw if isinstance(account, dict)] if isinstance(accounts_raw, list) else []
        )
        response = await build_query_coverage_answer(
            accounts_info=accounts_info,
            query_contract=session_query_contract,
            session=session,
            target_text=getattr(decision, "target_text", None),
        )
        return {
            "transaction_outcome": TransactionOutcome.OK,
            "response": response,
            "session_active": True,
            "flow_state": "complete",
            "resolver_message": None,
            "show_expanded": bool(session.get("show_expanded", False)),
            "current_page": session.get("current_page", 0),
            **step._semantic_trace_updates(decision),
        }

    elif cont_type == "explain_aggregate_scope":
        response_text = (getattr(decision, "response_text", None) or "").strip()
        contextual_hint = (getattr(decision, "contextual_hint", None) or "").strip()
        response = response_text or build_aggregate_scope_reply(
            session_query_contract=session_query_contract,
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
        if (
            drill_idx is None
            and surface_view is not None
            and len(getattr(surface_view, "items", []) or []) == 1
        ):
            drill_idx = 0

        selection_payload = None
        if surface_view is not None and message:
            selection_payload = find_selection_payload(surface_view, label=message)
        if selection_payload is None and drill_idx is not None:
            selection_payload = find_selection_payload(surface_view, index=drill_idx)
        selection_payload = _normalize_focused_aggregate_selection_payload(
            selection_payload,
            session_query_contract=session_query_contract,
            surface_view=surface_view,
            drill_idx=drill_idx,
        )

        if (
            session_query_contract is not None
            and selection_payload is not None
            and (
                selection_payload.selection_kind == "group_bucket"
                or bool(selection_payload.filters_patch)
                or selection_payload.time_patch is not None
            )
        ):
            query_fact_field: QueryFactField | None = None
            if drill_down_action == "answer_fact" and answer_fact_field is not None:
                query_fact_field = "counterparty" if answer_fact_field == "recipient" else answer_fact_field
            updates["query_contract"] = apply_selection_payload_to_query(
                session_query_contract,
                selection_payload,
                fact_field=query_fact_field,
                continuation_type=cont_type,
                continuation_delta_type=decision.delta_type,
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

    elif cont_type == "recipient_drill_down":
        recipient_name = decision.recipient_name
        if recipient_name and session_query_contract is not None:
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
            if selection_payload is not None and session_query_contract is not None:
                updates["query_contract"] = apply_selection_payload_to_query(
                    session_query_contract,
                    selection_payload,
                    fact_field=recipient_answer_fact_field,
                    continuation_type=cont_type,
                    continuation_delta_type=decision.delta_type,
                )
            else:
                new_filters = Filters(counterparty=[recipient_name])
                updates["query_contract"] = rebuild_query_contract(
                    session_query_contract,
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

    elif cont_type == "unclear":
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
            recovered_updates.update(step._semantic_trace_updates(decision))
            return recovered_updates
        return step._ambiguous_followup_updates(locale=locale, session=session)

    elif cont_type == "recheck":
        if session_query_contract is None:
            return step._ambiguous_followup_updates(locale=locale, session=session)
        updates["query_contract"] = rebuild_query_contract(
            session_query_contract,
            continuation_type=cont_type,
            continuation_delta_type=continuation_delta_type,
            conversational_prefix=decision.response_text,
        )
        updates["current_page"] = 0
        updates["show_expanded"] = False

    elif cont_type == "aggregate":
        aggregate_updates = await compile_aggregate_continuation_updates(
            step,
            decision=decision,
            state=state,
            today=today,
            language=locale,
            session_query_contract=session_query_contract,
            parse_result_to_updates=compiler_paths.parse_result_to_updates,
            parse_reasoner_extraction_to_updates=compiler_paths.parse_reasoner_extraction_to_updates,
        )
        if aggregate_updates is not None:
            return aggregate_updates
        return step._ambiguous_followup_updates(locale=locale, session=session)

    if "query_contract" in updates and updates.get("query_contract") is not session_query_contract:
        return step._append_query_session_transition(updates, "replace_session_new_query")

    return updates


def _normalize_focused_aggregate_selection_payload(
    selection_payload: SelectionPayload | None,
    *,
    session_query_contract: Any | None,
    surface_view: Any | None,
    drill_idx: int | None,
) -> SelectionPayload | None:
    """Repair stale focused aggregate payloads into scoped query payloads.

    Older persisted query sessions may have a focused beneficiary surface whose
    item payload still looks like a concrete transaction. The active contract
    and focused surface are enough to preserve the semantic selection without
    reading rendered text.
    """
    intent = getattr(session_query_contract, "intent", None)
    if (
        session_query_contract is None
        or intent not in {QueryIntent.BENEFICIARY_SUMMARY, QueryIntent.ANALYTICS_SUMMARY}
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
        return selection_payload

    surface_item = surface_items[drill_idx]
    label = str(
        getattr(selection_payload, "label", "") if selection_payload is not None else ""
    ).strip() or str(getattr(surface_item, "label", "") or "").strip()
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
        aggregation = getattr(session_query_contract, "aggregation", None)
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
