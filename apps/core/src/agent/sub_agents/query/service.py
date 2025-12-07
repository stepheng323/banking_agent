"""Query service for answering financial questions using Mono API."""

from typing import Dict, Any, List
from langchain_core.runnables import Runnable

from shared.clients.mono_client import MonoClient
from shared.repositories.user_repository import UserRepository
from shared.repositories.account_repository import AccountRepository
from apps.core.src.agent.sub_agents.query.parser import QueryParser


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

        for acc in accounts:
            if acc.get("is_default"):
                account = acc
                break
        
        if not account and accounts:
            account = accounts[0]
            
        if not account:
            return "You need to link a bank account before I can check your transactions."
        
        return await self.answer_question(account["account_id"], text)

    async def answer_question(
        self,
        account_id: str,
        question: str
    ) -> str:
        """
        Answer a financial question.
        
        Args:
            user_id: User ID
            account_id: Mono account ID
            question: Natural language question
            
        Returns:
            Answer as formatted text
        """
        try:
            params = await self.parser.parse(question)
            print(f"📊 Query params: {params}")
            
            transactions = await self.mono.get_transactions(
                account_id=account_id,
                from_date=params["date_range"]["from"],
                to_date=params["date_range"]["to"],
                transaction_type=params["transaction_type"] if params["transaction_type"] != "both" else None,
                narration=params["narration_filter"],
                limit=100
            )
            
            if not transactions:
                return self._no_transactions_response(params)
            
            result = self._aggregate(transactions, params)
            
            response = self._format_response(result, params)
            
            return response
            
        except Exception as e:
            print(f"Error answering question: {e}")
            return "I'm having trouble analyzing your transactions right now. Please try again."
    
    def _aggregate(self, transactions: List[Dict[str, Any]], params: Dict[str, Any]) -> Any:
        """Aggregate transactions based on query type."""
        query_type = params["query_type"]
        
        if query_type == "total_spent":
            return self._total_spent(transactions, params)
        elif query_type in ["search", "transaction_list"]:
            return self._transaction_list(transactions, params)
        else:
            return self._transaction_list(transactions, params)
    
    def _total_spent(self, transactions: List[Dict[str, Any]], params: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate total amount spent."""
        filtered = [t for t in transactions if t.get("type") == params["transaction_type"]] if params["transaction_type"] != "both" else transactions
        
        total = sum(t.get("amount", 0) for t in filtered)
        count = len(filtered)
        
        return {
            "total_kobo": total,
            "total_naira": total / 100,
            "transaction_count": count,
            "transaction_type": params["transaction_type"]
        }
    
    def _transaction_list(self, transactions: List[Dict[str, Any]], params: Dict[str, Any]) -> List[Dict[str, Any]]:
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
        date_range = params["date_range"]
        return f"I couldn't find any transactions from {date_range['from']} to {date_range['to']}."
    
    def _format_response(self, result: Any, params: Dict[str, Any]) -> str:
        """Format aggregated results into natural language."""
        query_type = params["query_type"]
        
        if query_type == "total_spent":
            return self._format_total_spent(result)
        elif query_type in ["search", "transaction_list"]:
            return self._format_transaction_list(result, params)
        else:
            return str(result)
    
    def _format_total_spent(self, result: Dict[str, Any]) -> str:
        """Format total spent response."""
        total = result["total_naira"]
        count = result["transaction_count"]
        tx_type = result["transaction_type"]
        
        type_str = "spent" if tx_type == "debit" else "received" if tx_type == "credit" else "transacted"
        
        return f"You {type_str} ₦{total:,.2f} across {count} transaction{'s' if count != 1 else ''}."
    
    def _format_transaction_list(self, result: List[Dict[str, Any]], params: Dict[str, Any]) -> str:
        """Format transaction list response."""
        if not result:
            return "No transactions found."
        
        lines = ["Here are your recent transactions:\n"]
        for i, t in enumerate(result, 1):
            type_emoji = "📤" if t["type"] == "debit" else "📥"
            lines.append(
                f"{i}. {type_emoji} {t['date']} - {t['narration'][:40]} - ₦{t['amount_naira']:,.2f}"
            )
        
        return "\n".join(lines)
