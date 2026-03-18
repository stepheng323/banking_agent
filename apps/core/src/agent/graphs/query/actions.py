"""Continuity actions for query flow (drill-down, receipts, etc)."""

from typing import Any

from apps.core.src.agent.graphs.query.models import QueryResult
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
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


def _build_query_transfer_handoff(item: Any) -> dict[str, Any]:
    metadata = item.metadata if isinstance(getattr(item, "metadata", None), dict) else {}

    recipient_name = str(metadata.get("recipient_name") or item.description or "").strip() or None
    recipient_account = (
        str(metadata.get("recipient_account_number") or metadata.get("recipient_account") or "").strip() or None
    )
    recipient_bank_name = str(metadata.get("recipient_bank_name") or metadata.get("bank_name") or "").strip() or None
    recipient_bank_code = str(metadata.get("recipient_bank_code") or "").strip() or None

    payload: dict[str, Any] = {
        "action": "send_money",
        "amount": item.amount,
        "narration": item.description,
        "recipient_name": recipient_name,
        "recipient_account": recipient_account,
        "recipient_bank_name": recipient_bank_name,
        "recipient_bank_code": recipient_bank_code,
        "skip_extraction": True,
    }
    return {k: v for k, v in payload.items() if v is not None and v != ""}


async def handle_drill_down(state: dict[str, Any]) -> TransactionResult:
    """Handle drill-down using index and action from classifier."""
    locale = LocaleManager.normalize(state.get("language")).value

    # Note: State here is the working state (with session merged)
    query_result = state.get("query_result")

    # Check if we have items
    # In V3 pipeline, query_result might be in the session part of state
    if isinstance(query_result, dict):
        query_result = QueryResult.model_validate(query_result)

    drill_down_index = state.get("selected_item_index", 0)
    drill_down_action = state.get("drill_down_action", "view_details")
    fact_field = state.get("fact_field")

    if not query_result or not query_result.items:
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            response=render_message("query.drill_down.no_items", locale),
        )

    index = max(0, min(drill_down_index, len(query_result.items) - 1))
    item = query_result.items[index]

    if drill_down_action == "answer_fact":
        metadata = item.metadata if isinstance(getattr(item, "metadata", None), dict) else {}
        response = None

        if fact_field == "amount":
            response = render_message("query.format.field_amount", locale, {"amount": f"₦{item.amount:,.2f}"})
        elif fact_field == "status":
            status = str(metadata.get("status") or "")
            if status:
                status_display = (
                    render_message("query.format.status_success", locale)
                    if status.lower() in ("success", "completed", "successful")
                    else render_message("query.format.status_pending_generic", locale, {"status": status.title()})
                )
                response = render_message("query.format.field_status", locale, {"status": status_display})
        elif fact_field == "bank":
            bank_name = str(metadata.get("bank_name") or "")
            if bank_name:
                response = render_message("query.format.field_bank", locale, {"bank_name": bank_name})
        elif fact_field == "date":
            response = render_message(
                "query.format.field_date",
                locale,
                {
                    "date": item.date.strftime("%B %d, %Y")
                    if item.date
                    else render_message("query.format.unknown", locale),
                },
            )
        elif fact_field == "recipient":
            recipient = str(metadata.get("recipient_name") or item.description or "").strip()
            if recipient:
                response = render_message(
                    "transfer.format.multi_source_summary.field_to",
                    locale,
                    {"recipient_name": recipient},
                )

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
        payload = _build_query_transfer_handoff(item)
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

    # VIEW DETAILS (Default)
    from apps.core.src.agent.graphs.query.services.formatter import QueryFormatter

    # Create single-item result for formatter to pick up "Detailed View" logic
    detail_result = QueryResult(summary_text="", items=[item], context_key=query_result.context_key)
    formatted = QueryFormatter.format(detail_result, show_expanded=True, locale=locale)

    return TransactionResult(
        outcome=TransactionOutcome.OK,
        response=formatted,
        patch={"session_active": True},
    )
