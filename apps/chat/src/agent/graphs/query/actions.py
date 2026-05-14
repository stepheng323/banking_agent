"""Continuity actions for query flow (drill-down, receipts, etc)."""

from typing import Any

from apps.chat.src.agent.graphs.query.models import QueryExecutionContract, QueryResult, QueryResultItem
from apps.chat.src.agent.graphs.query.services.answer_strategy import build_direct_fact_answer
from apps.chat.src.agent.graphs.query.services.contracts import build_query_transfer_handoff_payload
from apps.chat.src.agent.graphs.query.services.formatter import QueryFormatter
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.chat.src.agent.shared.query_contracts import SelectionPayload, SurfaceItemView, SurfaceView, SurfaceViewMode
from shared.i18n import LocaleManager, render_message
from shared.queue.factory import QueuePublisherFactory


def _resolve_transaction_type(item: Any, locale: str) -> tuple[str, str]:
    transaction_type = ""
    if isinstance(getattr(item, "metadata", None), dict):
        transaction_type = str(item.metadata.get("transaction_type") or "")
    transaction_type_display = (
        transaction_type.title() if transaction_type else render_message("query.common.transaction", locale)
    )
    return transaction_type, transaction_type_display


def _query_contract_from_state(state: dict[str, Any]) -> QueryExecutionContract | None:
    query_contract = state.get("query_contract")
    if isinstance(query_contract, QueryExecutionContract):
        return query_contract

    query_result = state.get("query_result")
    if isinstance(query_result, QueryResult):
        return query_result.query_contract
    if isinstance(query_result, dict):
        try:
            validated = QueryResult.model_validate(query_result)
            return validated.query_contract
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
        metadata = item.metadata if isinstance(getattr(item, "metadata", None), dict) else {}
        response = None

        if fact_field in {"amount", "bank", "date", "recipient", "counterparty"}:
            query_contract = _query_contract_from_state(state)
            answer_context = build_direct_fact_answer(
                item,
                query_contract=query_contract,
                fact_field="counterparty" if fact_field == "recipient" else fact_field,
                locale=locale,
            )
            lines = [answer_context.primary_text]
            if answer_context.secondary_text:
                lines.extend(["", answer_context.secondary_text])
            response = "\n".join(lines)
        elif fact_field == "status":
            status = str(metadata.get("status") or "")
            if status:
                status_display = (
                    render_message("query.format.status_success", locale)
                    if status.lower() in ("success", "completed", "successful")
                    else render_message("query.format.status_pending_generic", locale, {"status": status.title()})
                )
                response = render_message("query.format.field_status", locale, {"status": status_display})
        elif fact_field == "description":
            response = f"Description: {item.description}"
        elif fact_field == "reference":
            reference = str(metadata.get("transaction_id") or metadata.get("reference") or item.id or "").strip()
            if reference:
                response = f"Reference: {reference}"
        elif fact_field == "account":
            account = str(
                metadata.get("source_account_number")
                or metadata.get("account_number")
                or metadata.get("account")
                or metadata.get("bank_name")
                or ""
            ).strip()
            if account:
                response = f"Account: {account}"
        elif fact_field == "direction":
            direction = str(metadata.get("direction") or metadata.get("transaction_type") or metadata.get("type") or "").strip()
            if direction:
                response = f"Direction: {direction.title()}"
        elif fact_field == "category":
            category = str(metadata.get("resolved_category") or metadata.get("category") or "").strip()
            if category:
                response = f"Category: {category.replace('_', ' ').title()}"
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
            payload.get("recipient_account") and (payload.get("recipient_bank_name") or payload.get("recipient_bank_code"))
        )
        if not is_transfer_item:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=f"I can only resend transfer transactions. This looks like {transaction_type_display}.",
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
    query_contract = _query_contract_from_state(state)
    if query_contract is not None:
        query_contract = query_contract.model_copy(
            update={
                "answer_fact_field": None,
                "result_reference": None,
            }
        )

    detail_result = QueryResult(
        summary_text="",
        items=[item],
        context_key=query_result.context_key,
        query_contract=query_contract,
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
            },
        ),
    )
    formatted = QueryFormatter.format(detail_result, show_expanded=True, locale=locale)

    return TransactionResult(
        outcome=TransactionOutcome.OK,
        response=formatted,
        patch={
            "session_active": True,
            "query_result": detail_result,
            "selected_item_id": item.id,
        },
    )
