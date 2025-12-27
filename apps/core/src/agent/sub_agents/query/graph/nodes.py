"""Node functions for query flow graph."""

from typing import Dict, Any, Optional
import json

from langchain_core.runnables import Runnable

from shared.clients.providers.mono import MonoClient
from apps.core.src.agent.sub_agents.query.parser import QueryParser
from apps.core.src.agent.sub_agents.query.graph.state import QueryState
from apps.core.src.agent.sub_agents.query.validators import QueryValidator
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def parse_node(
    state: QueryState,
    parser: QueryParser
) -> Dict[str, Any]:
    """Parse user query into structured parameters."""
    message = state["message"]
    phone_number = state["phone_number"]
    
    try:
        params = await parser.parse(message)
        
        # Validate parsed parameters
        is_valid, error_msg = QueryValidator.validate(params, phone_number)
        if not is_valid:
            return {
                "flow_state": "error",
                "response": error_msg or "Invalid query parameters.",
            }
        
        return {
            "flow_state": "fetching",
            "query_type": params.get("query_type", "transaction_list"),
            "date_range": params.get("date_range", {}),
            "narration_filter": params.get("narration_filter"),
            "transaction_type": params.get("transaction_type", "both"),
            "limit": params.get("limit", 10),
            "current_page": 0,
            "page_size": params.get("limit", 10),
            "amount_check": params.get("amount_check"),
        }
    except Exception as e:
        logger.error("parse_node_error", error=str(e))
        return {
            "flow_state": "error",
            "response": "I couldn't understand your query. Could you rephrase it?"
        }


async def fetch_node(
    state: QueryState,
    mono_client: MonoClient
) -> Dict[str, Any]:
    """Fetch data from Mono API."""
    query_type = state.get("query_type", "transaction_list")
    account_id = state["account_id"]
    
    # Handle balance query
    if query_type == "balance":
        try:
            balance = await mono_client.get_balance(account_id)
            if "error" in balance:
                return {
                    "flow_state": "error",
                    "response": "I couldn't fetch your balance right now."
                }
            return {
                "flow_state": "formatting",
                "aggregated_result": {
                    "type": "balance",
                    "balance_naira": balance.get("balance_naira", 0),
                    "ledger_balance_naira": balance.get("ledger_balance_naira", 0),
                },
                "has_more": False,
            }
        except Exception as e:
            logger.error("fetch_balance_error", error=str(e))
            return {
                "flow_state": "error",
                "response": "I couldn't fetch your balance right now."
            }
    
    if query_type == "affordability":
        try:
            balance = await mono_client.get_balance(account_id)
            if "error" in balance:
                return {
                    "flow_state": "error",
                    "response": "I couldn't check your balance right now."
                }
            balance_naira = balance.get("balance_naira", 0)
            amount_check = state.get("amount_check", 0)
            can_afford = balance_naira >= amount_check
            shortfall = max(0, amount_check - balance_naira)
            
            return {
                "flow_state": "formatting",
                "aggregated_result": {
                    "type": "affordability",
                    "balance_naira": balance_naira,
                    "amount_check": amount_check,
                    "can_afford": can_afford,
                    "shortfall": shortfall,
                },
                "has_more": False,
            }
        except Exception as e:
            logger.error("fetch_affordability_error", error=str(e))
            return {
                "flow_state": "error",
                "response": "I couldn't check your balance right now."
            }
    
    try:
        date_range = state.get("date_range", {})
        tx_type = state.get("transaction_type", "both")
        
        fetch_limit = state.get("page_size", 10) * 3
        
        transactions = await mono_client.get_transactions(
            account_id=account_id,
            start=date_range.get("start"),
            end=date_range.get("end"),
            transaction_type=tx_type if tx_type != "both" else None,
            narration=state.get("narration_filter"),
            limit=fetch_limit
        )
        
        return {
            "flow_state": "aggregating",
            "cached_transactions": transactions,
            "total_results": len(transactions),
            "has_more": len(transactions) > state.get("page_size", 10),
        }
    except Exception as e:
        logger.error("fetch_transactions_error", error=str(e))
        return {
            "flow_state": "error",
            "response": "I couldn't fetch your transactions right now."
        }


async def aggregate_node(state: QueryState) -> Dict[str, Any]:
    """Aggregate transaction data based on query type."""
    from collections import defaultdict
    
    query_type = state.get("query_type", "transaction_list")
    transactions = state.get("cached_transactions", [])
    page = state.get("current_page", 0)
    page_size = state.get("page_size", 10)
    
    # Paginate
    start_idx = page * page_size
    end_idx = start_idx + page_size
    page_transactions = transactions[start_idx:end_idx]
    
    if query_type in ("total_spent", "total_received"):
        tx_type = "debit" if query_type == "total_spent" else "credit"
        filtered = [t for t in transactions if t.get("type") == tx_type]
        total_kobo = sum(t.get("amount", 0) for t in filtered)
        
        result = {
            "type": "total",
            "total_naira": total_kobo / 100,
            "transaction_count": len(filtered),
            "transaction_type": tx_type,
        }
    
    elif query_type in ("top_recipient", "top_sender"):
        tx_type = "debit" if query_type == "top_recipient" else "credit"
        filtered = [t for t in transactions if t.get("type") == tx_type]
        
        counterparty_totals = defaultdict(lambda: {"total": 0, "count": 0})
        for t in filtered:
            counterparty = _extract_counterparty(t.get("narration", "Unknown"))
            counterparty_totals[counterparty]["total"] += t.get("amount", 0)
            counterparty_totals[counterparty]["count"] += 1
        
        sorted_items = sorted(
            counterparty_totals.items(),
            key=lambda x: x[1]["total"],
            reverse=True
        )[:5]
        
        result = {
            "type": "top_counterparties",
            "items": [
                {"name": name, "total_naira": data["total"] / 100, "count": data["count"]}
                for name, data in sorted_items
            ],
            "transaction_type": tx_type,
        }
    
    elif query_type == "breakdown":
        # Group transactions by date for daily breakdown
        daily_totals = defaultdict(lambda: {"debit": 0, "credit": 0, "count": 0})
        
        for t in transactions:
            date = t.get("date", "")[:10]
            tx_type = t.get("type", "unknown")
            amount = t.get("amount", 0)
            
            if tx_type in ("debit", "credit"):
                daily_totals[date][tx_type] += amount
                daily_totals[date]["count"] += 1
        
        # Sort by date descending
        sorted_days = sorted(daily_totals.items(), key=lambda x: x[0], reverse=True)[:7]
        
        result = {
            "type": "breakdown",
            "days": [
                {
                    "date": date,
                    "spent_naira": data["debit"] / 100,
                    "received_naira": data["credit"] / 100,
                    "transaction_count": data["count"],
                }
                for date, data in sorted_days
            ],
            "total_spent_naira": sum(d["debit"] for _, d in sorted_days) / 100,
            "total_received_naira": sum(d["credit"] for _, d in sorted_days) / 100,
        }
    
    else:  # transaction_list, search
        result = {
            "type": "transaction_list",
            "transactions": [
                {
                    "date": t.get("date", "")[:10],
                    "narration": t.get("narration", "Unknown"),
                    "amount_naira": t.get("amount", 0) / 100,
                    "type": t.get("type", "unknown"),
                }
                for t in page_transactions
            ],
            "page": page,
            "total": len(transactions),
        }
    
    return {
        "flow_state": "formatting",
        "aggregated_result": result,
        "has_more": end_idx < len(transactions),
    }


async def paginate_node(state: QueryState) -> Dict[str, Any]:
    """Handle pagination - load next page."""
    current_page = state.get("current_page", 0)
    
    return {
        "flow_state": "aggregating",
        "current_page": current_page + 1,
    }


async def refine_node(
    state: QueryState,
    parser: QueryParser
) -> Dict[str, Any]:
    """Apply filter refinement to existing results."""
    new_filter = state.get("new_filter")
    
    if new_filter:
        return {
            "flow_state": "fetching",
            "narration_filter": new_filter,
            "current_page": 0,  # Reset pagination
        }
    
    return {"flow_state": "aggregating"}


async def format_node(
    state: QueryState,
    llm: Runnable
) -> Dict[str, Any]:
    """Format aggregated results into natural language response."""
    result = state.get("aggregated_result", {})
    language = state.get("language", "English")
    has_more = state.get("has_more", False)
    
    # Format based on result type
    result_type = result.get("type", "")
    
    if result_type == "balance":
        balance = result.get("balance_naira", 0)
        account_info = state.get("account_info", {})
        bank_name = account_info.get("bank_name", "") if account_info else ""
        
        if bank_name:
            response = f"💰 Your {bank_name} balance is **₦{balance:,.2f}**"
        else:
            response = f"💰 Your current balance is **₦{balance:,.2f}**"
    
    elif result_type == "total":
        total = result.get("total_naira", 0)
        count = result.get("transaction_count", 0)
        tx_type = result.get("transaction_type", "")
        
        emoji = "📤" if tx_type == "debit" else "📥"
        action = "spent" if tx_type == "debit" else "received"
        response = f"{emoji} You {action} **₦{total:,.2f}** across {count} transaction{'s' if count != 1 else ''}."
    
    elif result_type == "top_counterparties":
        items = result.get("items", [])
        tx_type = result.get("transaction_type", "")
        
        if not items:
            response = "No transactions found."
        else:
            title = "👥 **Top Recipients:**\n" if tx_type == "debit" else "👥 **Top Senders:**\n"
            lines = [title]
            for i, item in enumerate(items, 1):
                lines.append(f"{i}. {item['name']} - ₦{item['total_naira']:,.2f} ({item['count']} txn{'s' if item['count'] > 1 else ''})")
            response = "\n".join(lines)
    
    elif result_type == "transaction_list":
        transactions = result.get("transactions", [])
        
        if not transactions:
            response = "No transactions found."
        else:
            lines = ["📋 **Transactions:**\n"]
            for i, t in enumerate(transactions, 1):
                emoji = "📤" if t["type"] == "debit" else "📥"
                lines.append(f"{i}. {emoji} {t['date']} - {t['narration'][:30]} - ₦{t['amount_naira']:,.2f}")
            response = "\n".join(lines)
    
    elif result_type == "affordability":
        can_afford = result.get("can_afford", False)
        balance_naira = result.get("balance_naira", 0)
        amount_check = result.get("amount_check", 0)
        shortfall = result.get("shortfall", 0)
        
        if can_afford:
            response = f"✅ Yes, you can afford ₦{amount_check:,.0f}.\n\n💰 Your balance: ₦{balance_naira:,.2f}"
        else:
            response = f"❌ Not enough funds for ₦{amount_check:,.0f}.\n\n💰 Your balance: ₦{balance_naira:,.2f}\n📉 Shortfall: ₦{shortfall:,.2f}"
    
    elif result_type == "breakdown":
        days = result.get("days", [])
        total_spent = result.get("total_spent_naira", 0)
        total_received = result.get("total_received_naira", 0)
        
        if not days:
            response = "No activity found for this period."
        else:
            lines = ["📊 **Activity Summary:**\n"]
            for day in days:
                date_str = day["date"]
                spent = day["spent_naira"]
                received = day["received_naira"]
                count = day["transaction_count"]
                lines.append(f"• {date_str}: 📤 ₦{spent:,.0f} | 📥 ₦{received:,.0f} ({count} txns)")
            
            lines.append(f"\n**Totals:** 📤 ₦{total_spent:,.0f} spent | 📥 ₦{total_received:,.0f} received")
            response = "\n".join(lines)
    
    else:
        response = "Query completed."
    
    # Add pagination hint if more results
    if has_more and result_type == "transaction_list":
        response += "\n\n_Reply 'show more' to see more transactions._"
    
    return {
        "flow_state": "complete",
        "response": response,
        "session_active": has_more,  # Keep session active if more results
    }


def _extract_counterparty(narration: str) -> str:
    """Extract counterparty name from narration."""
    if not narration:
        return "Unknown"
    
    narration = narration.strip()
    prefixes = ["Transfer to ", "Transfer from ", "Payment to ", "From ", "To "]
    for prefix in prefixes:
        if narration.startswith(prefix):
            narration = narration[len(prefix):]
            break
    
    if len(narration) > 30:
        narration = narration[:30] + "..."
    
    return narration
