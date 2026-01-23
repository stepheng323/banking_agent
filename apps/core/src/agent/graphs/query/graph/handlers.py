"""Continuation handlers for query flow."""

import json
from datetime import datetime
from typing import Any

from apps.core.src.agent.graphs.query.models import QueryResult, QueryResultItem
from shared.cache.redis_client import RedisClient

QUEUE_NAME = "banking:receipt_jobs"


async def handle_drill_down(state: dict[str, Any]) -> dict[str, Any]:
    """Handle drill-down using index and action from classifier."""

    query_result = state.get("query_result")
    drill_down_index = state.get("drill_down_index", 0)
    drill_down_action = state.get("drill_down_action", "view_details")

    if not query_result or not query_result.items:
        return {"response": "No items to drill down into."}

    index = max(0, min(drill_down_index, len(query_result.items) - 1))
    item = query_result.items[index]

    if drill_down_action == "get_receipt":
        transaction_type = item.metadata.get("transaction_type", "") if item.metadata else ""
        if transaction_type != "transfer":
            return {
                "response": f"Receipts are only available for bank transfers. This is a {transaction_type.title() if transaction_type else 'transaction'}.",
                "session_active": True,
            }

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

            return {
                "response": "I'm generating your receipt now. I'll send it to you as an image shortly.",
                "session_active": True,
            }
        except Exception:
            return {
                "response": "Sorry, I couldn't generate the receipt at this moment. Please try again later.",
                "session_active": True,
            }

    if drill_down_action == "report_issue":
        return {
            "response": (
                f"I understand you have an issue with this transaction:\n\n"
                f"*{item.description}* - ₦{item.amount:,.2f}\n\n"
                f"Please describe the issue:\n"
                f"1️⃣ Transaction failed but I was debited\n"
                f"2️⃣ I don't recognize this transaction\n"
                f"3️⃣ Wrong amount was charged\n"
                f"4️⃣ Other issue\n\n"
                f"_Reply with the number or describe your issue._"
            ),
            "pending_support_item": item.model_dump() if hasattr(item, "model_dump") else item,
            "session_active": True,
        }

    lines = ["*Transaction Details*", ""]

    amount_str = f"₦{item.amount:,.2f}"
    lines.append(f"*Amount:* {amount_str}")
    lines.append(f"*Description:* {item.description}")
    lines.append(f"*Date:* {item.date.strftime('%B %d, %Y') if item.date else 'Unknown'}")

    if item.metadata:
        tx_type = item.metadata.get("type", "")
        if tx_type:
            direction = "Outgoing (Debit)" if tx_type == "debit" else "Incoming (Credit)"
            lines.append(f"*Type:* {direction}")

        bank_name = item.metadata.get("bank_name", "")
        if bank_name:
            lines.append(f"*Bank:* {bank_name}")

        transaction_type = item.metadata.get("transaction_type", "")
        if transaction_type:
            lines.append(f"*Category:* {transaction_type.title()}")

    if item.id:
        lines.append(f"*Ref:* {item.id}")

    lines.append("")
    lines.append("_Reply: 'receipt' for proof | 'issue' to report a problem_")

    return {
        "response": "\n".join(lines),
        "session_active": True,
        "focused_item": item.model_dump() if hasattr(item, "model_dump") else None,
    }


def handle_recipient_drill_down(state: dict[str, Any]) -> dict[str, Any]:
    """Handle drill-down into a specific recipient's transactions."""
    query_result = state.get("query_result")
    recipient_name = state.get("recipient_name", "").lower()

    if not query_result or not query_result.items:
        return {
            "response": "No recipient data available.",
            "session_active": False,
        }

    matched_item = None
    for item in query_result.items:
        if item.description.lower() == recipient_name or recipient_name in item.description.lower():
            matched_item = item
            break

    if not matched_item:
        return {
            "response": f"I couldn't find '{recipient_name}' in your top recipients. Try typing the exact name.",
            "session_active": True,
        }

    transactions = matched_item.metadata.get("transactions", []) if matched_item.metadata else []

    if not transactions:
        return {
            "response": f"No transaction details available for {matched_item.description}.",
            "session_active": True,
        }

    items = []
    for i, t in enumerate(transactions[:5]):
        items.append(
            QueryResultItem(
                id=t.get("id", str(i))[:8],
                description=t.get("narration", "Transaction"),
                amount=abs(t.get("amount", 0)) / 100,
                date=datetime.strptime(t.get("date", "")[:10], "%Y-%m-%d").date() if t.get("date") else None,
                metadata={"bank_name": t.get("bank_name", ""), "type": t.get("type", "")},
            )
        )

    count = matched_item.metadata.get("count", len(transactions))
    total = matched_item.amount

    result = QueryResult(
        summary_text=f"*{matched_item.description}* — ₦{total:,.0f} ({count}x)\n",
        items=items,
    )

    return {
        "query_result": result,
        "show_expanded": True,
        "current_page": 0,
        "response": None,
        "session_active": True,
    }


def handle_local_filter(state: dict[str, Any]) -> dict[str, Any]:
    """Apply filter to current expanded items without re-fetching."""
    query_result = state.get("query_result")
    filters = state.get("filters")

    if not query_result or not query_result.items:
        return {"response": "No items to filter.", "session_active": False}

    filtered_items = list(query_result.items)

    if filters:
        if filters.account_filter:
            bank_filter = filters.account_filter.lower()
            filtered_items = [
                item
                for item in filtered_items
                if item.metadata and bank_filter in item.metadata.get("bank_name", "").lower()
            ]

        if filters.transaction_type:
            filtered_items = [
                item
                for item in filtered_items
                if item.metadata and item.metadata.get("type") == filters.transaction_type
            ]

    if not filtered_items:
        filter_desc = filters.account_filter if filters and filters.account_filter else "those criteria"
        return {
            "response": f"No transactions matching {filter_desc}.",
            "session_active": True,
        }

    filter_label = ""
    if filters and filters.account_filter:
        filter_label = f" ({filters.account_filter})"

    new_result = QueryResult(
        summary_text=f"*Filtered Transactions*{filter_label}\n",
        items=filtered_items,
    )

    return {
        "query_result": new_result,
        "current_page": 0,
    }


def record_query_success(state: dict[str, Any]) -> dict[str, Any]:
    """Record successful query execution and reset clarification counter."""
    query = state.get("query")
    if query and hasattr(query, "model_dump"):
        state["last_successful_query"] = {
            "query": query.model_dump(),
            "filters": state.get("filters"),
            "time_range": query.time_range.model_dump() if query.time_range else None,
        }

    state["clarification_attempts"] = 0
    state["confidence_level"] = "high"

    return state


def handle_unclear(state: dict[str, Any]) -> dict[str, Any]:
    """Handle unclear message - increment counter and offer clarification or recovery."""
    from apps.core.src.agent.graphs.query.continuity import (
        build_soft_clarification,
        get_recovery_message,
        should_offer_recovery,
    )

    attempts = state.get("clarification_attempts", 0) + 1
    state["clarification_attempts"] = attempts

    if should_offer_recovery(attempts, max_attempts=2):
        return {
            "response": get_recovery_message(),
            "clarification_attempts": attempts,
            "session_active": True,
        }

    query_result = state.get("query_result")
    if query_result and query_result.items:
        clarification = build_soft_clarification(query_result.items[:5])
        return {
            "response": clarification,
            "clarification_attempts": attempts,
            "session_active": True,
        }

    return {
        "response": "I didn't quite catch that. Could you rephrase?",
        "clarification_attempts": attempts,
        "session_active": True,
    }
