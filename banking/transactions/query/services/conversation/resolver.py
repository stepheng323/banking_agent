"""Deterministic update builder for active query-result conversations."""

from __future__ import annotations

from typing import Any

from banking.runtime.results import TransactionOutcome
from banking.transactions.query.continuations.clarification_state import (
    build_selection_clarification_updates,
    clarification_candidate,
)
from banking.transactions.query.contracts import SelectionPayload, SurfaceView
from banking.transactions.query.models.domain import QueryFrame, QueryResult
from banking.transactions.query.models.extraction import ClarificationOperation
from banking.transactions.query.services.conversation.targets import (
    decision_has_target_reference,
    resolve_query_target,
    resolve_requested_fact_field,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def build_query_conversation_updates(
    *,
    surface_view: SurfaceView | None,
    query_result: QueryResult | None,
    decision: Any,
    text: str,
    query_frames: list[QueryFrame] | None = None,
    locale: str = "en",
    session: dict[str, Any] | None = None,
    turn_id: str | None = None,
) -> dict[str, Any] | None:
    """Build grounded active-query updates for visible item follow-ups."""
    if getattr(decision, "decision", None) != "continuation":
        return None
    continuation_type = getattr(decision, "continuation_type", None)
    if continuation_type in {"filter_delta", "time_delta", "aggregate", "coverage"}:
        return None
    if (
        continuation_type not in {"drill_down", "recipient_drill_down"}
        and not decision_has_target_reference(decision, text)
    ):
        return None

    matches, miss_response = resolve_query_target(
        surface_view=surface_view,
        query_result=query_result,
        query_frames=query_frames,
        decision=decision,
        text=text,
    )
    requested_field = getattr(decision, "requested_field", None) or getattr(decision, "fact_field", None)
    if miss_response:
        logger.info(
            "query_conversation_grounding",
            outcome="miss",
            continuation_type=continuation_type,
            requested_field=requested_field,
            has_current_surface=surface_view is not None,
            frame_count=len(query_frames or []),
        )
        return {
            "transaction_outcome": TransactionOutcome.OK,
            "response": miss_response,
            "session_active": True,
            "flow_state": "complete",
        }
    if len(matches) > 1:
        logger.info(
            "query_conversation_grounding",
            outcome="ambiguous",
            continuation_type=continuation_type,
            match_count=len(matches),
            requested_field=requested_field,
            has_current_surface=surface_view is not None,
            frame_count=len(query_frames or []),
        )
        candidates = [
            clarification_candidate(payload=match.item.payload, label=match.item.label, frame_id=match.frame_id)
            for match in matches[:5]
        ]
        return build_selection_clarification_updates(
            candidates=candidates,
            operation=ClarificationOperation(
                continuation_type="drill_down",
                drill_down_action=getattr(decision, "drill_down_action", None) or "view_details",
                fact_field=resolve_requested_fact_field(decision),
                grounded_operation=getattr(decision, "grounded_operation", None),
            ),
            query_contract=query_result.query_contract if query_result is not None else None,
            locale=locale,
            session=session or {},
            turn_id=turn_id,
        )
    if not matches:
        logger.info(
            "query_conversation_grounding",
            outcome="fell_through",
            continuation_type=continuation_type,
            requested_field=requested_field,
            has_current_surface=surface_view is not None,
            frame_count=len(query_frames or []),
        )
        return None

    match = matches[0]
    payload: SelectionPayload = match.item.payload
    if _is_focused_aggregate_scope(surface_view=surface_view, query_result=query_result, payload=payload):
        return None
    if payload.selection_kind == "group_bucket" or bool(payload.filters_patch) or payload.time_patch is not None:
        return None

    action = getattr(decision, "drill_down_action", None) or "view_details"
    field = resolve_requested_fact_field(decision)
    if field is not None:
        action = "answer_fact"

    logger.info(
        "query_conversation_grounding",
        outcome="selected",
        continuation_type=continuation_type,
        action=action,
        requested_field=field,
        selected_from_frame=match.frame_id is not None,
        match_index=match.index,
        has_current_surface=surface_view is not None,
        frame_count=len(query_frames or []),
    )

    return {
        "flow_state": "executing",
        "continuation_type": "drill_down",
        "continuation_delta_type": getattr(decision, "delta_type", None),
        "resolver_message": None,
        "selected_payload": payload,
        "selected_item_index": match.index,
        "selected_item_id": match.item.id,
        **({"selected_query_item": match.query_item} if match.query_item is not None else {}),
        **({"selected_frame_id": match.frame_id} if match.frame_id is not None else {}),
        "drill_down_action": action,
        **({"fact_field": "recipient" if field == "counterparty" else field} if field else {}),
    }


def _is_focused_aggregate_scope(
    *,
    surface_view: SurfaceView | None,
    query_result: QueryResult | None,
    payload: SelectionPayload,
) -> bool:
    if payload.selection_kind in {"beneficiary", "group_bucket", "account"}:
        return True

    context = surface_view.context if surface_view is not None and isinstance(surface_view.context, dict) else {}
    focus_type = str(context.get("focus_type") or "").strip()
    if focus_type in {"beneficiary", "group_bucket", "account"}:
        return True

    query_contract = query_result.query_contract if query_result is not None else None
    if query_contract is None:
        return False
    if query_contract.intent == query_contract.intent.BENEFICIARY_SUMMARY:
        return True
    return bool(query_contract.aggregation is not None and query_contract.aggregation.group_by is not None)


__all__ = ["build_query_conversation_updates"]
