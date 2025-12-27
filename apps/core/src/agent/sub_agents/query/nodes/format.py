"""Format nodes for query flow - LLM-based response generation."""

from typing import Dict, Any
import json

from langchain_core.runnables import Runnable

from apps.core.src.agent.sub_agents.query.graph.state import QueryState
from shared.utils.logging import get_logger

logger = get_logger(__name__)


FORMAT_RESPONSE_PROMPT = """You are a banking assistant formatting query results for a WhatsApp message.

RULES:
- Be concise and clear
- Use appropriate emojis sparingly
- Format currency as ₦X,XXX.XX
- Use WhatsApp markdown: *bold*, _italic_
- Never give financial advice
- For affordability, say "covers this amount" not "you can afford"
- Keep responses under 300 words
- Use bullet points for lists

USER'S QUERY: {query}

DATA TO FORMAT:
{data}

ADDITIONAL CONTEXT:
- Language: {language}
- Has more results: {has_more}

Format this data into a natural, helpful response."""


async def format_node(
    state: QueryState,
    llm: Runnable
) -> Dict[str, Any]:
    """Format aggregated results using LLM for natural language response."""
    result = state.get("aggregated_result", {})
    language = state.get("language", "English")
    has_more = state.get("has_more", False)
    message = state.get("message", "")
    result_type = result.get("type", "")
    
    # Handle simple prompt responses without LLM
    if result_type == "affordability_prompt":
        response = f"🛒 *Price Needed*\n\n{result.get('message', 'Please specify an amount.')}"
        return {
            "flow_state": "complete",
            "response": response,
            "session_active": False,
        }
    
    # Use LLM to format response
    try:
        prompt = FORMAT_RESPONSE_PROMPT.format(
            query=message,
            data=json.dumps(result, indent=2, default=str),
            language=language,
            has_more=has_more,
        )
        
        response = await llm.ainvoke(prompt)
        
        # Extract text from response
        if hasattr(response, 'content'):
            response_text = response.content
        else:
            response_text = str(response)
        
        # Add pagination hint if needed
        if has_more and result_type == "transaction_list":
            response_text += "\n\n_Reply 'show more' to see more transactions._"
        
        return {
            "flow_state": "complete",
            "response": response_text,
            "session_active": has_more,
        }
    
    except Exception as e:
        logger.error("format_llm_error", error=str(e))
        # Fallback to basic formatting
        return {
            "flow_state": "complete",
            "response": _fallback_format(result, has_more),
            "session_active": has_more,
        }


def _fallback_format(result: Dict[str, Any], has_more: bool) -> str:
    """Fallback formatting if LLM fails."""
    result_type = result.get("type", "")
    
    if result_type == "balance":
        balance = result.get("balance_naira", 0)
        return f"💰 Your current balance is *₦{balance:,.2f}*"
    
    elif result_type == "affordability":
        balance = result.get("balance_naira", 0)
        amount = result.get("amount_check", 0)
        can_afford = result.get("can_afford", False)
        
        if can_afford:
            remaining = balance - amount
            return f"💰 *Balance Check*\n\nAmount: ₦{amount:,.0f}\nYour balance: ₦{balance:,.2f}\n\n✅ Your balance covers this amount.\n_Remaining: ₦{remaining:,.0f}_"
        else:
            shortfall = result.get("shortfall", 0)
            return f"💰 *Balance Check*\n\nAmount: ₦{amount:,.0f}\nYour balance: ₦{balance:,.2f}\n\n⚠️ This exceeds your current balance.\n📉 Shortfall: ₦{shortfall:,.0f}"
    
    elif result_type == "total":
        total = result.get("total_naira", 0)
        count = result.get("transaction_count", 0)
        tx_type = result.get("transaction_type", "")
        emoji = "📤" if tx_type == "debit" else "📥"
        action = "spent" if tx_type == "debit" else "received"
        return f"{emoji} You {action} *₦{total:,.2f}* across {count} transaction{'s' if count != 1 else ''}."
    
    elif result_type == "transaction_list":
        transactions = result.get("transactions", [])
        if not transactions:
            return "No transactions found."
        
        lines = ["📋 *Transactions:*\n"]
        for i, t in enumerate(transactions[:10], 1):
            emoji = "📤" if t.get("type") == "debit" else "📥"
            lines.append(f"{i}. {emoji} {t.get('date', '')} - {t.get('narration', '')[:25]} - ₦{t.get('amount_naira', 0):,.0f}")
        
        response = "\n".join(lines)
        if has_more:
            response += "\n\n_Reply 'show more' to see more._"
        return response
    
    elif result_type == "breakdown":
        days = result.get("days", [])
        if not days:
            return "No activity found."
        
        lines = ["📊 *Activity Summary:*\n"]
        for day in days:
            lines.append(f"• {day['date']}: 📤 ₦{day['spent_naira']:,.0f} | 📥 ₦{day['received_naira']:,.0f}")
        
        total_spent = result.get("total_spent_naira", 0)
        total_received = result.get("total_received_naira", 0)
        lines.append(f"\n*Totals:* 📤 ₦{total_spent:,.0f} | 📥 ₦{total_received:,.0f}")
        return "\n".join(lines)
    
    elif result_type == "top_counterparties":
        items = result.get("items", [])
        if not items:
            return "No transactions found."
        
        tx_type = result.get("transaction_type", "")
        title = "👥 *Top Recipients:*\n" if tx_type == "debit" else "👥 *Top Senders:*\n"
        lines = [title]
        for i, item in enumerate(items, 1):
            lines.append(f"{i}. {item['name']} - ₦{item['total_naira']:,.0f} ({item['count']} txns)")
        return "\n".join(lines)
    
    return "Query completed."
