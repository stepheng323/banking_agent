"""LangGraph graph for query flow."""

from typing import Optional, Dict, Any
from functools import partial

from langchain_core.runnables import Runnable
from langgraph.graph import StateGraph, END

from shared.clients.mono_client import MonoClient
from shared.cache.redis_client import RedisClient
from apps.core.src.agent.sub_agents.query.parser import QueryParser
from apps.core.src.agent.sub_agents.query.graph.state import QueryState
from apps.core.src.agent.sub_agents.query.graph.nodes import (
    parse_node,
    fetch_node,
    aggregate_node,
    paginate_node,
    refine_node,
    format_node,
)
from apps.core.src.agent.sub_agents.query.graph.routing import (
    route_after_parse,
    route_after_fetch,
    detect_continuation_type,
    extract_filter_term,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)

# Session TTL in seconds (5 minutes)
SESSION_TTL = 300


class QueryFlowGraph:
    """LangGraph-based query flow with pagination and refinement support."""

    def __init__(
        self,
        llm: Runnable,
        mono_client: MonoClient,
        redis_client: Optional[RedisClient] = None,
    ):
        """
        Initialize query flow graph.

        Args:
            llm: Language model for parsing and formatting
            mono_client: Mono API client
            redis_client: Redis client for session state
        """
        self.llm = llm
        self.mono = mono_client
        self.redis = redis_client or RedisClient.get_client()
        self.parser = QueryParser(llm)
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """Build the query flow graph."""
        graph = StateGraph(QueryState)

        # Add nodes with bound dependencies
        graph.add_node("parse", partial(parse_node, parser=self.parser))
        graph.add_node("fetch", partial(fetch_node, mono_client=self.mono))
        graph.add_node("aggregate", aggregate_node)
        graph.add_node("paginate", paginate_node)
        graph.add_node("refine", partial(refine_node, parser=self.parser))
        graph.add_node("format", partial(format_node, llm=self.llm))
        graph.add_node("error", self._error_node)

        # Add edges
        graph.set_entry_point("parse")
        
        graph.add_conditional_edges(
            "parse",
            route_after_parse,
            {
                "fetch": "fetch",
                "error": "error",
            }
        )
        
        graph.add_conditional_edges(
            "fetch",
            route_after_fetch,
            {
                "aggregate": "aggregate",
                "format": "format",
                "error": "error",
            }
        )
        
        graph.add_edge("aggregate", "format")
        graph.add_edge("paginate", "aggregate")
        graph.add_edge("refine", "fetch")
        graph.add_edge("format", END)
        graph.add_edge("error", END)

        return graph.compile()

    async def _error_node(self, state: QueryState) -> Dict[str, Any]:
        """Handle error state."""
        response = state.get("response", "Something went wrong. Please try again.")
        return {
            "flow_state": "error",
            "response": response,
            "session_active": False,
        }

    async def run(
        self,
        phone_number: str,
        message: str,
        user_ctx: Dict[str, Any],
        message_id: str = "",
    ) -> str:
        """
        Run the query flow.

        Args:
            phone_number: User's phone number
            message: User's query message
            user_ctx: User context with profile, accounts, language
            message_id: Optional message ID

        Returns:
            Response string
        """
        # Get account info
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
        
        account_id = account.get("account_id") or account.get("mono_account_id")
        if not account_id:
            return "I couldn't find your linked account. Please try linking again."

        # Check for existing session
        session_key = f"query:session:{phone_number}"
        session_data = await self._load_session(session_key)
        
        # Detect if this is a continuation
        session_active = session_data.get("session_active", False) if session_data else False
        continuation_type = detect_continuation_type(message, session_active)
        
        # Build initial state
        if continuation_type == "show_more" and session_data:
            # Continue from saved state
            state = session_data
            state["message"] = message
            state["flow_state"] = "paginating"
            state["continuation_type"] = "show_more"
            
            # Run pagination flow
            result = await self._run_continuation(state, "paginate")
        
        elif continuation_type == "filter" and session_data:
            # Apply filter to saved state
            state = session_data
            state["message"] = message
            state["flow_state"] = "refining"
            state["continuation_type"] = "filter"
            state["new_filter"] = extract_filter_term(message)
            
            # Run refine flow
            result = await self._run_continuation(state, "refine")
        
        else:
            # New query
            state: QueryState = {
                "phone_number": phone_number,
                "message": message,
                "message_id": message_id,
                "flow_state": "parsing",
                "session_active": False,
                "query_type": "transaction_list",
                "date_range": {},
                "narration_filter": None,
                "transaction_type": "both",
                "limit": 10,
                "account_id": account_id,
                "account_ids": [acc.get("account_id") or acc.get("mono_account_id") for acc in accounts if acc.get("account_id") or acc.get("mono_account_id")],
                "current_account_index": 0,
                "account_info": account,
                "current_page": 0,
                "page_size": 10,
                "total_results": 0,
                "has_more": False,
                "cached_transactions": [],
                "aggregated_result": None,
                "language": user_ctx.get("language", "English"),
                "response": "",
            }
            
            # Run full graph
            result = await self.graph.ainvoke(state)

        # Save session if active
        if result.get("session_active"):
            await self._save_session(session_key, result)
        else:
            await self._clear_session(session_key)

        return result.get("response", "Query completed.")

    async def _run_continuation(self, state: QueryState, start_node: str) -> Dict[str, Any]:
        """Run graph from a specific node for continuations."""
        # For continuations, we run specific nodes directly
        if start_node == "paginate":
            state = {**state, **(await paginate_node(state))}
            state = {**state, **(await aggregate_node(state))}
            state = {**state, **(await format_node(state, self.llm))}
        elif start_node == "refine":
            state = {**state, **(await refine_node(state, self.parser))}
            state = {**state, **(await fetch_node(state, self.mono))}
            state = {**state, **(await aggregate_node(state))}
            state = {**state, **(await format_node(state, self.llm))}
        
        return state

    async def _load_session(self, key: str) -> Optional[Dict[str, Any]]:
        """Load session state from Redis."""
        try:
            import json
            data = await self.redis.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error("load_session_error", error=str(e))
        return None

    async def _save_session(self, key: str, state: Dict[str, Any]) -> None:
        """Save session state to Redis."""
        try:
            import json
            # Only save serializable fields
            save_state = {
                k: v for k, v in state.items()
                if k in (
                    "phone_number", "query_type", "date_range", "narration_filter",
                    "transaction_type", "limit", "account_id", "account_ids",
                    "current_account_index", "account_info", "current_page",
                    "page_size", "total_results", "has_more", "cached_transactions",
                    "language", "session_active"
                )
            }
            await self.redis.set(key, json.dumps(save_state), ex=SESSION_TTL)
        except Exception as e:
            logger.error("save_session_error", error=str(e))

    async def _clear_session(self, key: str) -> None:
        """Clear session state from Redis."""
        try:
            await self.redis.delete(key)
        except Exception as e:
            logger.error("clear_session_error", error=str(e))
