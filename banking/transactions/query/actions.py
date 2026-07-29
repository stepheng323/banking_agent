"""Continuity actions for query flow (drill-down, receipts, etc)."""

from typing import Any

from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transactions.query.contracts import SelectionPayload, SurfaceItemView, SurfaceView, SurfaceViewMode
from banking.transactions.query.conversation_focus import advance_focus
from banking.transactions.query.models.conversation import QueryFocus
from banking.transactions.query.models.domain import (
    QueryRequest,
    QueryResult,
    QueryResultItem,
)
from banking.transactions.query.presentation.surface_builder import build_query_transfer_handoff_payload
from banking.transactions.query.services.answers.fact_answer import build_direct_fact_answer
from shared.queue.factory import QueuePublisherFactory


def _resolve_transaction_type(item: Any, locale: str) -> tuple[str, str]:
    transaction_type = ""
    if isinstance(getattr(item, "metadata", None), dict):
        transaction_type = str(item.metadata.get("transaction_type") or "")
    transaction_type_display = (
        transaction_type.title() if transaction_type else render_message("query.common.transaction", locale)
    )
    return transaction_type, transaction_type_display


def _query_request_from_state(state: dict[str, Any]) -> QueryRequest | None:
    query_request = state.get("query_request")
    if isinstance(query_request, QueryRequest):
        return query_request

    query_result = state.get("query_result")
    if isinstance(query_result, QueryResult):
        return query_result.query_request
    if isinstance(query_result, dict):
        try:
            validated = QueryResult.model_validate(query_result)
            return validated.query_request
        except Exception:
            return None
    return None


def _coerce_query_result(state: dict[str, Any]) -> QueryResult | None:
    query_result = state.get("query_result")
    if isinstance(query_result, QueryResult):
        return query_result
    if isinstance(query_result, dict):
        try:
            return QueryResult.model_validate(query_result)
        except Exception:
            return None
    return None


def _coerce_selection_payload(raw_payload: Any) -> SelectionPayload | None:
    if isinstance(raw_payload, SelectionPayload):
        return raw_payload
    if isinstance(raw_payload, dict):
        try:
            return SelectionPayload.model_validate(raw_payload)
        except Exception:
            return None
    return None


def _coerce_focus(raw_focus: Any) -> QueryFocus | None:
    if isinstance(raw_focus, QueryFocus):
        return raw_focus
    if isinstance(raw_focus, dict):
        try:
            return QueryFocus.model_validate(raw_focus)
        except Exception:
            return None
    return None


def _resolve_selected_item(query_result: QueryResult, state: dict[str, Any]) -> Any | None:
    selected_query_item = state.get("selected_query_item")
    if isinstance(selected_query_item, QueryResultItem):
        return selected_query_item
    if isinstance(selected_query_item, dict):
        try:
            return QueryResultItem.model_validate(selected_query_item)
        except Exception:
            pass

    items = query_result.items or []
    if not items:
        return None

    selection_payload = _coerce_selection_payload(state.get("selected_payload"))
    if selection_payload is not None:
        if selection_payload.entity_id:
            for item in items:
                if item.id == selection_payload.entity_id:
                    return item
        normalized_label = selection_payload.label.strip().lower()
        if normalized_label:
            for item in items:
                if item.description.strip().lower() == normalized_label:
                    return item
                metadata: dict[str, Any] = item.metadata if isinstance(item.metadata, dict) else {}
                recipient_name = str(metadata.get("recipient_name") or "").strip().lower()
                if recipient_name and recipient_name == normalized_label:
                    return item
        return None

    selected_item_id = state.get("selected_item_id")
    if isinstance(selected_item_id, str) and selected_item_id.strip():
        for item in items:
            if item.id == selected_item_id:
                return item
        return None

    if "selected_item_index" in state:
        drill_down_index = state.get("selected_item_index")
        if isinstance(drill_down_index, int) and 0 <= drill_down_index < len(items):
            return items[drill_down_index]
        return None

    return items[0] if len(items) == 1 else None


def _resolve_selected_payload(query_result: QueryResult, state: dict[str, Any], item: Any) -> SelectionPayload | None:
    selection_payload = _coerce_selection_payload(state.get("selected_payload"))
    if selection_payload is not None:
        return selection_payload

    surface_view = query_result.surface_view
    if surface_view is not None:
        selected_item_id = None
        if isinstance(surface_view.context, dict):
            selected_item_id = surface_view.context.get("selected_item_id")
        for surface_item in surface_view.items:
            if selected_item_id and surface_item.id == selected_item_id:
                return surface_item.payload
            if surface_item.id == getattr(item, "id", None):
                return surface_item.payload
    return None


async def handle_drill_down(state: dict[str, Any]) -> TransactionResult:
    """Handle drill-down using index and action from classifier."""
    locale = LocaleManager.normalize(state.get("language")).value

    query_result = _coerce_query_result(state)

    drill_down_action = state.get("drill_down_action", "view_details")
    fact_field = state.get("fact_field")

    if not query_result or not query_result.items:
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            response=render_message("query.drill_down.no_items", locale),
        )

    item = _resolve_selected_item(query_result, state)
    if item is None:
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            response=render_message("query.drill_down.no_items", locale),
        )

    if drill_down_action == "answer_fact":
        response = None

        if fact_field in {
            "amount",
            "bank",
            "date",
            "recipient",
            "counterparty",
            "status",
            "reference",
            "account",
            "direction",
            "category",
            "description",
        }:
            query_request = _query_request_from_state(state)
            answer_context = build_direct_fact_answer(
                item,
                query_request=query_request,
                fact_field="counterparty" if fact_field == "recipient" else fact_field,
                locale=locale,
                is_followup=True,
            )
            lines = [answer_context.primary_text]
            if answer_context.secondary_text:
                lines.extend(["", answer_context.secondary_text])
            response = "\n".join(lines)

        if response:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=response,
                patch={"session_active": True},
            )

    if drill_down_action == "get_receipt":
        transaction_type, transaction_type_display = _resolve_transaction_type(item, locale)
        if transaction_type != "transfer":
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=render_message(
                    "query.receipt.only_transfer",
                    locale,
                    {"transaction_type": transaction_type_display},
                ),
                patch={"session_active": True},
            )

        try:
            publisher = QueuePublisherFactory.get_async_publisher()
            transfer_data = {
                "amount": item.amount,
                "narration": item.description,
                "recipient": {
                    "name": item.metadata.get("recipient_name") or item.description,
                    "bank_name": item.metadata.get(
                        "bank_name",
                        render_message("query.receipt.bank_unknown", locale),
                    ),
                    "account_number": item.metadata.get(
                        "recipient_account",
                        render_message("query.receipt.na", locale),
                    ),
                },
                "source": {"account_name": render_message("query.receipt.user_account", locale)},
            }

            if item.metadata.get("recipient_name"):
                transfer_data["recipient"]["name"] = item.metadata.get("recipient_name")

            payload = {
                "phone_number": state.get("phone_number"),
                "transaction_reference": (
                    item.metadata.get("transaction_id")
                    if isinstance(item.metadata, dict) and item.metadata.get("transaction_id")
                    else item.id
                )
                or render_message("query.receipt.na", locale),
                **transfer_data,
            }

            job = {"payload": payload, "signal_key": None}

            await publisher.publish(topic="receipt.process", message=job)

            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=render_message("query.receipt.generating", locale),
                patch={"session_active": True},
            )
        except Exception:
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                response=render_message("query.receipt.failed", locale),
                patch={"session_active": True},
            )

    if drill_down_action == "re_transfer":
        transaction_type, transaction_type_display = _resolve_transaction_type(item, locale)
        payload = build_query_transfer_handoff_payload(item) or {}
        is_transfer_item = transaction_type == "transfer" or bool(
            payload.get("recipient_account")
            and (payload.get("recipient_bank_name") or payload.get("recipient_bank_code"))
        )
        if not is_transfer_item:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=f"I can only resend transfer transactions. This is {transaction_type_display}.",
                patch={"session_active": True},
            )
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response="Understood. I will start that transfer again now.",
            patch={
                "session_active": False,
                "query_transfer_handoff": payload,
            },
        )

    if drill_down_action == "report_issue":
        response = render_message(
            "query.report_issue.template",
            locale,
            {
                "description": item.description,
                "amount": f"{item.amount:,.2f}",
            },
        )
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response=response,
            patch={
                "session_active": True,
            },
        )

    selected_payload = _resolve_selected_payload(query_result, state, item)
    query_request = _query_request_from_state(state)
    if query_request is not None:
        query_request = query_request.model_copy(
            update={
                "answer_fact_field": None,
                "result_reference": None,
            }
        )

    detail_result = QueryResult(
        # A detail view is a focused lens over the active list, not a new list.
        # Keep the list summary and pagination state so a later "is that
        # everything?" still answers about the list the user originally saw.
        summary_text=query_result.summary_text,
        items=[item],
        context_key=query_result.context_key,
        has_more=query_result.has_more,
        query_request=query_request,
        surface_view=SurfaceView(
            mode=SurfaceViewMode.DIRECT_ANSWER,
            items=[
                SurfaceItemView(
                    id=item.id,
                    label=item.description,
                    amount=item.amount,
                    payload=selected_payload
                    or SelectionPayload(
                        selection_kind="transaction",
                        entity_type="transaction",
                        entity_id=item.id,
                        label=item.description,
                    ),
                    metadata=item.metadata or {},
                )
            ],
            context={
                "type": "single_transaction",
                "selected_item_id": item.id,
                "parent_visible_count": min(len(query_result.items or []), 5),
            },
        ),
    )
    if query_request is not None:
        detail_result.conversation_focus = advance_focus(
            request=query_request,
            previous=_coerce_focus(state.get("active_focus")) or query_result.conversation_focus,
            continuation_type="drill_down",
            selected_payload=selected_payload,
            source_frame_id=state.get("selected_frame_id"),
            turn_id=state.get("turn_id"),
        )
    try:
        intro = render_message("query.drill_down.details_intro", locale)
    except Exception:
        intro = "Here are the details for that transaction:"

    date_str = item.date.strftime("%B %d, %Y") if item.date else "N/A"
    amount_str = f"₦{abs(float(item.amount or 0.0)):,.2f}"

    bank_name = item.metadata.get("bank_name") or item.metadata.get("recipient_bank_name") or "N/A"
    counterparty = item.metadata.get("counterparty") or item.metadata.get("recipient_name") or "N/A"
    status = str(item.metadata.get("status") or "successful").capitalize()
    reference = item.metadata.get("reference") or item.metadata.get("transaction_id") or "N/A"

    lines = [
        intro,
        "",
        f"**{item.description}**",
        f"**Amount:** {amount_str}",
        f"**Date:** {date_str}",
        f"**Counterparty:** {counterparty}",
        f"**Bank:** {bank_name}",
        f"**Status:** {status}",
        f"**Reference:** {reference}",
    ]
    formatted = "\n".join(lines)

    return TransactionResult(
        outcome=TransactionOutcome.OK,
        response=formatted,
        patch={
            "session_active": True,
            "query_result": detail_result,
            "selected_item_id": item.id,
            "active_focus": detail_result.conversation_focus,
        },
    )
