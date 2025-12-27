"""Fetch nodes for query flow - data retrieval from Mono API."""

from typing import Dict, Any
from datetime import datetime, timedelta

from shared.clients.providers.mono import MonoClient
from apps.core.src.agent.sub_agents.query.graph.state import QueryState
from apps.core.src.agent.sub_agents.query.prices import (
    lookup_item_price, is_variable_item
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def fetch_balance(
    state: QueryState,
    mono_client: MonoClient
) -> Dict[str, Any]:
    """Fetch account balance."""
    account_id = state["account_id"]
    
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


async def fetch_affordability(
    state: QueryState,
    mono_client: MonoClient
) -> Dict[str, Any]:
    """Fetch data for affordability analysis."""
    account_id = state["account_id"]
    
    try:
        # Resolve amount from item_name if present
        item_name = state.get("item_name")
        amount_check = state.get("amount_check")
        price_source = None
        
        if item_name and not amount_check:
            item_data = lookup_item_price(item_name)
            if item_data:
                amount_check = item_data["price"]
                price_source = "lookup"
            elif is_variable_item(item_name):
                return {
                    "flow_state": "formatting",
                    "aggregated_result": {
                        "type": "affordability_prompt",
                        "item_name": item_name,
                        "message": f"Prices for {item_name} vary widely. What's the specific amount you're considering?",
                    },
                    "needs_price_input": True,
                    "has_more": False,
                }
            else:
                return {
                    "flow_state": "formatting",
                    "aggregated_result": {
                        "type": "affordability_prompt",
                        "item_name": item_name,
                        "message": f"I don't have a current price for \"{item_name}\". What's the approximate cost?",
                    },
                    "needs_price_input": True,
                    "has_more": False,
                }
        
        if not amount_check:
            return {
                "flow_state": "error",
                "response": "Please specify an amount to check.",
            }
        
        # Get balance
        balance = await mono_client.get_balance(account_id)
        if "error" in balance:
            return {
                "flow_state": "error",
                "response": "I couldn't check your balance right now."
            }
        balance_naira = balance.get("balance_naira", 0)
        
        analysis_type = state.get("analysis_type", "immediate")
        projection_months = state.get("projection_months")
        
        # Calculate historical stats for relative/simulated analysis
        avg_daily_spend = None
        avg_monthly_net = None
        
        if analysis_type in ("relative", "simulated"):
            end_date = datetime.now()
            start_date = end_date - timedelta(days=30)
            
            transactions = await mono_client.get_transactions(
                account_id=account_id,
                start=start_date.strftime("%Y-%m-%d"),
                end=end_date.strftime("%Y-%m-%d"),
                limit=500
            )
            
            if transactions:
                total_spent = sum(t.get("amount", 0) for t in transactions if t.get("type") == "debit")
                total_received = sum(t.get("amount", 0) for t in transactions if t.get("type") == "credit")
                avg_daily_spend = (total_spent / 100) / 30
                avg_monthly_net = (total_received - total_spent) / 100
        
        # Build result
        can_afford = balance_naira >= amount_check
        shortfall = max(0, amount_check - balance_naira)
        remaining = balance_naira - amount_check
        
        result = {
            "type": "affordability",
            "analysis_type": analysis_type,
            "balance_naira": balance_naira,
            "amount_check": amount_check,
            "can_afford": can_afford,
            "shortfall": shortfall,
            "remaining": remaining,
            "percentage_of_balance": (amount_check / balance_naira * 100) if balance_naira > 0 else 0,
            "item_name": item_name,
            "price_source": price_source,
        }
        
        if analysis_type == "relative" and avg_daily_spend:
            result["avg_daily_spend"] = avg_daily_spend
            result["days_equivalent"] = int(amount_check / avg_daily_spend) if avg_daily_spend > 0 else 0
        
        if analysis_type == "simulated" and projection_months:
            projected_balance = balance_naira
            if avg_monthly_net:
                projected_balance = balance_naira + (avg_monthly_net * projection_months)
            result["projection_months"] = projection_months
            result["projected_balance"] = projected_balance
            result["projected_can_afford"] = projected_balance >= amount_check
            result["avg_monthly_net"] = avg_monthly_net
        
        return {
            "flow_state": "formatting",
            "aggregated_result": result,
            "avg_daily_spend": avg_daily_spend,
            "avg_monthly_net": avg_monthly_net,
            "has_more": False,
        }
    except Exception as e:
        logger.error("fetch_affordability_error", error=str(e))
        return {
            "flow_state": "error",
            "response": "I couldn't check your balance right now."
        }


async def fetch_transactions(
    state: QueryState,
    mono_client: MonoClient
) -> Dict[str, Any]:
    """Fetch transactions for analysis."""
    account_id = state["account_id"]
    
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


async def fetch_node(
    state: QueryState,
    mono_client: MonoClient
) -> Dict[str, Any]:
    """Main fetch node - routes to specific fetch functions."""
    query_type = state.get("query_type", "transaction_list")
    
    if query_type == "balance":
        return await fetch_balance(state, mono_client)
    elif query_type == "affordability":
        return await fetch_affordability(state, mono_client)
    else:
        return await fetch_transactions(state, mono_client)
