"""Continuity actions for query flow (drill-down, receipts, etc)."""

import json
from typing import Any

from apps.core.src.agent.graphs.query.models import QueryResult
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.cache.redis_client import RedisClient

QUEUE_NAME = "banking:receipt_jobs"


async def handle_drill_down(state: dict[str, Any]) -> TransactionResult:
    """Handle drill-down using index and action from classifier."""

    # Note: State here is the working state (with session merged)
    query_result = state.get("query_result")

    # Check if we have items
    # In V3 pipeline, query_result might be in the session part of state
    if isinstance(query_result, dict):
        query_result = QueryResult.model_validate(query_result)

    drill_down_index = state.get("selected_item_index", 0)
    drill_down_action = state.get("drill_down_action", "view_details")

    if not query_result or not query_result.items:
        return TransactionResult(outcome=TransactionOutcome.FAILED, response="No items to drill down into.")

    index = max(0, min(drill_down_index, len(query_result.items) - 1))
    item = query_result.items[index]

    if drill_down_action == "get_receipt":
        transaction_type = item.metadata.get("transaction_type", "") if item.metadata else ""
        if transaction_type != "transfer":
             return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=f"Receipts are only available for bank transfers. This is a {transaction_type.title() if transaction_type else 'transaction'}.",
                patch={"session_active": True}
            )

        try:
            redis_client = RedisClient.get_client()
            transfer_data = {
                "amount": item.amount,
                "narration": item.description,
                "recipient": {
                    "name": item.metadata.get("recipient_name") or item.description,
                    "bank_name": item.metadata.get("bank_name", "Unknown Bank"),
                    "account_number": item.metadata.get("recipient_account", "N/A"),
                },
                "source": {"account_name": "User Account"},
            }

            if item.metadata.get("recipient_name"):
                transfer_data["recipient"]["name"] = item.metadata.get("recipient_name")

            payload = {
                "phone_number": state.get("phone_number"),
                "transaction_reference": item.id or "N/A",
                **transfer_data,
            }

            job = {"payload": payload, "signal_key": None}

            await redis_client.rpush(QUEUE_NAME, json.dumps(job))

            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response="I'm generating your receipt now. I'll send it to you as an image shortly.",
                patch={"session_active": True},
            )
        except Exception:
             return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                response="Sorry, I couldn't generate the receipt at this moment. Please try again later.",
                patch={"session_active": True}
            )

    if drill_down_action == "report_issue":
        response = (
            f"I understand you have an issue with this transaction:\n\n"
            f"*{item.description}* - ₦{item.amount:,.2f}\n\n"
            f"Please describe the issue:\n"
            f"1️⃣ Transaction failed but I was debited\n"
            f"2️⃣ I don't recognize this transaction\n"
            f"3️⃣ Wrong amount was charged\n"
            f"4️⃣ Other issue\n\n"
            f"_Reply with the number or describe your issue._"
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
    detail_result = QueryResult(
        summary_text="",
        items=[item],
        context_key=query_result.context_key
    )
    formatted = QueryFormatter.format(detail_result, show_expanded=True)

    return TransactionResult(
        outcome=TransactionOutcome.OK,
        response=formatted,
        patch={"session_active": True},
    )
