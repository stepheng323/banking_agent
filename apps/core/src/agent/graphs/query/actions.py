"""Continuity actions for query flow (drill-down, receipts, etc)."""

import json
from collections.abc import Awaitable
from typing import Any, cast

from apps.core.src.agent.graphs.query.models import QueryResult
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.cache.redis_client import RedisClient
from shared.i18n import LocaleManager, render_message

QUEUE_NAME = "banking:receipt_jobs"


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

    if not query_result or not query_result.items:
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            response=render_message("query.drill_down.no_items", locale),
        )

    index = max(0, min(drill_down_index, len(query_result.items) - 1))
    item = query_result.items[index]

    if drill_down_action == "get_receipt":
        transaction_type = item.metadata.get("transaction_type", "") if item.metadata else ""
        transaction_type_display = (
            transaction_type.title() if transaction_type else render_message("query.common.transaction", locale)
        )
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
            redis_client = RedisClient.get_client()
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
                "transaction_reference": item.id or render_message("query.receipt.na", locale),
                **transfer_data,
            }

            job = {"payload": payload, "signal_key": None}

            push_result = redis_client.rpush(QUEUE_NAME, json.dumps(job))
            if not isinstance(push_result, int):
                await cast(Awaitable[int], push_result)

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
                "pending_support_item": item.model_dump() if hasattr(item, "model_dump") else item,
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
