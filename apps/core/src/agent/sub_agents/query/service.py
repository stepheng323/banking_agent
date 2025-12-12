"""Query service for answering financial questions using Mono API."""

from typing import Dict, Any, List, Optional
import json
from collections import defaultdict
from langchain_core.runnables import Runnable

from shared.clients.mono_client import MonoClient
from shared.repositories.user_repository import UserRepository
from shared.repositories.account_repository import AccountRepository
from apps.core.src.agent.sub_agents.query.parser import QueryParser
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class QueryService:
    """Service for answering financial questions about user transactions."""

    def __init__(
        self,
        llm: Runnable,
        mono_client: MonoClient,
        user_repo: UserRepository,
        account_repo: AccountRepository
    ):
        """
        Initialize query service.

        Args:
            llm: Language model for parsing and formatting
            mono_client: Mono API client
            user_repo: User repository
            account_repo: Account repository
        """
        self.llm = llm
        self.mono = mono_client
        self.user_repo = user_repo
        self.account_repo = account_repo
        self.parser = QueryParser(llm)

    async def handle_query(
        self,
        text: str,
        user_ctx: Dict[str, Any]
    ) -> str:
        """
        Handle query intent.

        Args:
            text: User's query text
            user_ctx: User context (profile, accounts, etc.)

        Returns:
            Response message
        """
        profile = user_ctx.get("profile")
        if not profile:
            return "I couldn't find your profile. Please contact support."

        accounts = user_ctx.get("accounts", [])
        account = None

        # Find default account or use first available
        for acc in accounts:
            if acc.get("is_default"):
                account = acc
                break

        if not account and accounts:
            account = accounts[0]

        if not account:
            return "You need to link a bank account before I can check your transactions."

        language = user_ctx.get("language", "English")
        account_id = account.get("account_id") or account.get("mono_account_id")
        
        if not account_id:
            return "I couldn't find your linked account. Please try linking again."

        return await self.answer_question(account_id, text, language, account)

    async def answer_question(
        self,
        account_id: str,
        question: str,
        language: str = "English",
        account_info: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Answer a financial question.

        Args:
            account_id: Mono account ID
            question: Natural language question
            language: User's preferred language
            account_info: Optional account metadata

        Returns:
            Answer as formatted text
        """
        try:
            params = await self.parser.parse(question)
            query_type = params.get("query_type", "transaction_list")
            logger.info("processing_query", query_type=query_type)

            # Handle balance query separately
            if query_type == "balance":
                return await self._handle_balance_query(account_id, account_info, language)

            # Fetch transactions for other query types
            transactions = await self.mono.get_transactions(
                account_id=account_id,
                start=params["date_range"]["start"],
                end=params["date_range"]["end"],
                transaction_type=params["transaction_type"] if params["transaction_type"] != "both" else None,
                narration=params["narration_filter"],
                limit=params.get("limit", 100)
            )

            if not transactions:
                return self._no_transactions_response(params)

            result = self._aggregate(transactions, params)
            response = await self._format_response(result, params, language)

            return response

        except Exception as e:
            logger.error("query_error", error=str(e), exc_info=True)
            return "I'm having trouble analyzing your transactions right now. Please try again."

    async def _handle_balance_query(
        self,
        account_id: str,
        account_info: Optional[Dict[str, Any]],
        language: str
    ) -> str:
        """Handle balance inquiry."""
        try:
            balance = await self.mono.get_balance(account_id)
            
            if "error" in balance:
                return "I couldn't fetch your balance right now. Please try again later."

            balance_naira = balance.get("balance_naira", 0)
            bank_name = account_info.get("bank_name", "") if account_info else ""
            
            # Format based on language
            if language.lower() in ("english", "en"):
                if bank_name:
                    return f"💰 Your {bank_name} balance is **₦{balance_naira:,.2f}**"
                return f"💰 Your current balance is **₦{balance_naira:,.2f}**"
            
            # Use LLM for other languages
            prompt = f"Translate to {language}: Your current balance is ₦{balance_naira:,.2f}"
            result = await self.llm.ainvoke(prompt)
            return result.content if hasattr(result, 'content') else str(result)

        except Exception as e:
            logger.error("balance_query_error", error=str(e))
            return "I couldn't fetch your balance right now. Please try again later."

    def _aggregate(self, transactions: List[Dict[str, Any]], params: Dict[str, Any]) -> Any:
        """Aggregate transactions based on query type."""
        query_type = params["query_type"]

        if query_type == "total_spent":
            return self._total_amount(transactions, "debit", params)
        elif query_type == "total_received":
            return self._total_amount(transactions, "credit", params)
        elif query_type == "top_recipient":
            return self._top_counterparties(transactions, "debit", params)
        elif query_type == "top_sender":
            return self._top_counterparties(transactions, "credit", params)
        elif query_type == "search":
            return self._transaction_list(transactions, params)
        else:
            return self._transaction_list(transactions, params)

    def _total_amount(
        self,
        transactions: List[Dict[str, Any]],
        tx_type: str,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Calculate total amount for a transaction type."""
        filtered = [t for t in transactions if t.get("type") == tx_type]

        total_kobo = sum(t.get("amount", 0) for t in filtered)
        count = len(filtered)

        return {
            "type": "total",
            "total_kobo": total_kobo,
            "total_naira": total_kobo / 100,
            "transaction_count": count,
            "transaction_type": tx_type,
            "date_range": params.get("date_range")
        }

    def _top_counterparties(
        self,
        transactions: List[Dict[str, Any]],
        tx_type: str,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Get top counterparties by transaction amount."""
        filtered = [t for t in transactions if t.get("type") == tx_type]
        
        # Group by narration (counterparty)
        counterparty_totals = defaultdict(lambda: {"total": 0, "count": 0})
        
        for t in filtered:
            narration = t.get("narration", "Unknown")
            # Extract counterparty name from narration
            counterparty = self._extract_counterparty(narration)
            counterparty_totals[counterparty]["total"] += t.get("amount", 0)
            counterparty_totals[counterparty]["count"] += 1

        # Sort by total amount descending
        sorted_counterparties = sorted(
            counterparty_totals.items(),
            key=lambda x: x[1]["total"],
            reverse=True
        )[:params.get("limit", 5)]

        items = [
            {
                "name": name,
                "total_naira": data["total"] / 100,
                "count": data["count"]
            }
            for name, data in sorted_counterparties
        ]

        return {
            "type": "top_counterparties",
            "items": items,
            "transaction_type": tx_type,
            "date_range": params.get("date_range")
        }

    def _extract_counterparty(self, narration: str) -> str:
        """Extract counterparty name from transaction narration."""
        if not narration:
            return "Unknown"
        
        # Common patterns to clean up
        narration = narration.strip()
        
        # Remove common prefixes
        prefixes = ["Transfer to ", "Transfer from ", "Payment to ", "From ", "To "]
        for prefix in prefixes:
            if narration.startswith(prefix):
                narration = narration[len(prefix):]
                break
        
        # Truncate long narrations
        if len(narration) > 30:
            narration = narration[:30] + "..."
        
        return narration

    def _transaction_list(
        self,
        transactions: List[Dict[str, Any]],
        params: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Return list of transactions."""
        limit = params.get("limit", 10)
        return [
            {
                "date": t.get("date", "")[:10],
                "narration": t.get("narration", "Unknown"),
                "amount_kobo": t.get("amount", 0),
                "amount_naira": t.get("amount", 0) / 100,
                "type": t.get("type", "unknown"),
                "category": t.get("category", "other")
            }
            for t in transactions[:limit]
        ]

    def _no_transactions_response(self, params: Dict[str, Any]) -> str:
        """Response when no transactions found."""
        date_range = params.get("date_range", {})
        start = date_range.get("start", "")
        end = date_range.get("end", "")
        
        if params.get("narration_filter"):
            return f"I couldn't find any transactions matching '{params['narration_filter']}' from {start} to {end}."
        return f"I couldn't find any transactions from {start} to {end}."

    async def _format_response(
        self,
        result: Any,
        params: Dict[str, Any],
        language: str = "English"
    ) -> str:
        """Format aggregated results into natural language."""
        query_type = params["query_type"]

        # Use deterministic formatting for English
        if language.lower() in ("english", "en"):
            if isinstance(result, dict):
                if result.get("type") == "total":
                    return self._format_total(result)
                elif result.get("type") == "top_counterparties":
                    return self._format_top_counterparties(result)
            elif isinstance(result, list):
                return self._format_transaction_list(result, params)

        # Use LLM for other languages
        context = {
            "query_type": query_type,
            "data": result,
            "params": params,
            "language": language
        }

        prompt = f"""
You are a banking assistant. Summarize the following transaction data for the user.
Reply in {language}. Keep it concise and helpful. Use emojis like 📤 for debit and 📥 for credit.

Data:
{json.dumps(context, indent=2, default=str)}

If the data list is empty, say no transactions were found matching the criteria.
"""
        try:
            response = await self.llm.ainvoke(prompt)
            if hasattr(response, 'content'):
                return response.content
            return str(response)
        except Exception:
            # Fallback to English format
            if isinstance(result, dict):
                if result.get("type") == "total":
                    return self._format_total(result)
                elif result.get("type") == "top_counterparties":
                    return self._format_top_counterparties(result)
            elif isinstance(result, list):
                return self._format_transaction_list(result, params)
            return str(result)

    def _format_total(self, result: Dict[str, Any]) -> str:
        """Format total spent/received response."""
        total = result["total_naira"]
        count = result["transaction_count"]
        tx_type = result["transaction_type"]

        if tx_type == "debit":
            emoji = "📤"
            action = "spent"
        else:
            emoji = "📥"
            action = "received"

        return f"{emoji} You {action} **₦{total:,.2f}** across {count} transaction{'s' if count != 1 else ''}."

    def _format_top_counterparties(self, result: Dict[str, Any]) -> str:
        """Format top recipients/senders response."""
        items = result.get("items", [])
        tx_type = result["transaction_type"]

        if not items:
            return "No transactions found."

        if tx_type == "debit":
            title = "👥 **Top Recipients:**\n"
        else:
            title = "👥 **Top Senders:**\n"

        lines = [title]
        for i, item in enumerate(items, 1):
            lines.append(
                f"{i}. {item['name']} - ₦{item['total_naira']:,.2f} ({item['count']} txn{'s' if item['count'] > 1 else ''})"
            )

        return "\n".join(lines)

    def _format_transaction_list(
        self,
        result: List[Dict[str, Any]],
        params: Dict[str, Any]
    ) -> str:
        """Format transaction list response."""
        if not result:
            return "No transactions found."

        lines = ["📋 **Recent Transactions:**\n"]
        for i, t in enumerate(result, 1):
            type_emoji = "📤" if t["type"] == "debit" else "📥"
            lines.append(
                f"{i}. {type_emoji} {t['date']} - {t['narration'][:35]} - ₦{t['amount_naira']:,.2f}"
            )

        return "\n".join(lines)
