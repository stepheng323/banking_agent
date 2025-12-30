"""LangGraph graph for query flow."""

import json
from datetime import date
from functools import partial
from typing import Any

import redis.asyncio as redis
from langchain_core.runnables import Runnable
from langgraph.graph import END, StateGraph

from apps.core.src.agent.sub_agents.query.continuity import ContinuationClassifier
from apps.core.src.agent.sub_agents.query.executor import QueryExecutor
from apps.core.src.agent.sub_agents.query.graph.nodes.execute import execute_node
from apps.core.src.agent.sub_agents.query.graph.nodes.format import format_node
from apps.core.src.agent.sub_agents.query.graph.nodes.parse import (
    classify_continuation_node,
    paginate_node,
    parse_node,
)
from apps.core.src.agent.sub_agents.query.graph.state import QueryState
from apps.core.src.agent.sub_agents.query.parser import QueryParser
from apps.core.src.agent.tools.account_selection.mandate_validator import validate_mandate_status
from shared.clients.abstractions.banking import BankingDataProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)

SESSION_TTL = 300


def route_after_parse(state: QueryState) -> str:
    """Route after parsing based on state."""
    flow_state = state.get("flow_state", "")
    if flow_state == "error":
        return "error"
    if flow_state == "clarification_needed":
        return "end"
    return "execute"


def route_after_execute(state: QueryState) -> str:
    """Route after execution based on state."""
    flow_state = state.get("flow_state", "")
    if flow_state == "error":
        return "error"
    return "format"


def route_continuation(state: QueryState) -> str:
    """Route continuation based on classified type."""
    cont_type = state.get("continuation_type")
    if cont_type == "show_more":
        return "paginate"
    elif cont_type in ("time_delta", "filter_delta"):
        return "execute"
    elif cont_type == "drill_down":
        return "format"
    return "parse"


class QueryFlowGraph:
    """LangGraph-based query flow with NormalizedQuery and QueryExecutor."""

    def __init__(
        self,
        llm: Runnable,
        banking_provider: BankingDataProvider,
        redis_client: redis.Redis,
    ):
        self.llm = llm
        self.provider = banking_provider
        self.redis = redis_client
        self.parser = QueryParser(llm)
        self.executor = QueryExecutor(banking_provider)
        self.continuation_classifier = ContinuationClassifier(llm)
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """Build the query flow graph."""
        graph = StateGraph(QueryState)

        graph.add_node("parse", partial(parse_node, parser=self.parser))
        graph.add_node(
            "classify_continuation",
            partial(
                classify_continuation_node,
                classifier=self.continuation_classifier,
                today=date.today().isoformat(),
            ),
        )
        graph.add_node("execute", partial(execute_node, executor=self.executor))
        graph.add_node("paginate", paginate_node)
        graph.add_node("format", partial(format_node, llm=self.llm))
        graph.add_node("error", self._error_node)

        graph.set_entry_point("parse")

        graph.add_conditional_edges(
            "parse",
            route_after_parse,
            {
                "execute": "execute",
                "error": "error",
                "end": END,
            },
        )

        graph.add_conditional_edges(
            "execute",
            route_after_execute,
            {
                "format": "format",
                "error": "error",
            },
        )

        graph.add_conditional_edges(
            "classify_continuation",
            route_continuation,
            {
                "paginate": "paginate",
                "execute": "execute",
                "format": "format",
                "parse": "parse",
            },
        )

        graph.add_edge("paginate", "execute")
        graph.add_edge("format", END)
        graph.add_edge("error", END)

        return graph.compile()

    async def _error_node(self, state: QueryState) -> dict[str, Any]:
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
        user_ctx: dict[str, Any],
        message_id: str = "",
    ) -> str:
        """Run the query flow."""
        accounts = user_ctx.get("accounts", [])
        account = next((a for a in accounts if a.get("is_default")), accounts[0] if accounts else None)

        if not account:
            return "You need to link a bank account before I can check your transactions."

        is_valid, error, _ = validate_mandate_status(account)
        if not is_valid:
            return f"⚠️ {error}"

        account_id = account.get("account_id") or account.get("mono_account_id")
        if not account_id:
            return "I couldn't find your linked account. Please try linking again."

        session_key = f"query:session:{phone_number}"
        session_data = await self._load_session(session_key)
        session_active = session_data.get("session_active", False) if session_data else False

        if session_active and session_data:
            # Use classify_continuation_node via direct call for continuations
            state: QueryState = {
                **session_data,
                "message": message,
                "message_id": message_id,
            }
            cont_result = await classify_continuation_node(
                state,
                self.continuation_classifier,
                date.today().isoformat(),
            )
            state.update(cont_result)

            if state.get("continuation_type") == "show_more":
                state.update(await paginate_node(state))
                state.update(await execute_node(state, self.executor))
                state.update(await format_node(state, self.llm))
            elif state.get("continuation_type") in ("time_delta", "filter_delta"):
                state.update(await execute_node(state, self.executor))
                state.update(await format_node(state, self.llm))
            elif state.get("continuation_type") == "drill_down":
                # Resolve drill-down from previous result
                drill_result = await self._handle_drill_down(state)
                state.update(drill_result)
                state.update(await format_node(state, self.llm))
            else:
                # New query
                state = await self._run_new_query(
                    state, phone_number, message, message_id, account_id, accounts, user_ctx
                )
            result = state
        else:
            state = self._create_initial_state(phone_number, message, message_id, account_id, accounts, user_ctx)
            result = await self.graph.ainvoke(state)

        if result.get("session_active"):
            await self._save_session(session_key, result)
        else:
            await self._clear_session(session_key)

        return result.get("response", "Query completed.")

    def _create_initial_state(
        self,
        phone_number: str,
        message: str,
        message_id: str,
        account_id: str,
        accounts: list,
        user_ctx: dict,
    ) -> QueryState:
        """Create initial state for new query."""
        return {
            "phone_number": phone_number,
            "message": message,
            "message_id": message_id,
            "flow_state": "parsing",
            "session_active": False,
            "account_id": account_id,
            "account_ids": [
                acc.get("account_id") or acc.get("mono_account_id")
                for acc in accounts
                if acc.get("account_id") or acc.get("mono_account_id")
            ],
            "accounts": accounts,
            "current_account_index": 0,
            "account_info": accounts[0] if accounts else None,
            "current_page": 0,
            "page_size": 10,
            "total_results": 0,
            "has_more": False,
            "cached_transactions": [],
            "language": user_ctx.get("language", "English"),
            "response": "",
        }

    async def _handle_drill_down(self, state: QueryState) -> dict[str, Any]:
        """Handle drill-down by resolving item from previous result."""
        from apps.core.src.agent.sub_agents.query.continuity import resolve_drill_down
        from apps.core.src.agent.sub_agents.query.models import QueryResult

        query_result = state.get("query_result")
        reference = state.get("drill_down_ref", "")

        if not query_result or not query_result.items:
            return {"response": "No items to drill down into."}

        item = resolve_drill_down(reference, query_result)

        if not item:
            return {"response": "I couldn't find that item. Try specifying differently."}

        # Create a focused result for the single item
        focused_result = QueryResult(
            summary_text=f"Details for {item.description}",
            items=[item],
            has_more=False,
        )

        return {
            "query_result": focused_result,
            "has_more": False,
        }

    async def _run_new_query(
        self,
        state: QueryState,
        phone_number: str,
        message: str,
        message_id: str,
        account_id: str,
        accounts: list,
        user_ctx: dict,
    ) -> QueryState:
        """Run a completely new query."""
        new_state = self._create_initial_state(phone_number, message, message_id, account_id, accounts, user_ctx)
        return await self.graph.ainvoke(new_state)

    async def _load_session(self, key: str) -> dict[str, Any] | None:
        """Load session state from Redis and restore Pydantic models."""
        from apps.core.src.agent.sub_agents.query.models import NormalizedQuery, QueryResult

        try:
            data = await self.redis.get(key)
            if not data:
                return None

            session = json.loads(data)

            # Restore NormalizedQuery
            if session.get("query") and isinstance(session["query"], dict):
                try:
                    session["query"] = NormalizedQuery.model_validate(session["query"])
                except Exception as e:
                    logger.warning("query_restore_error", error=str(e))
                    session["query"] = None

            # Restore QueryResult
            if session.get("query_result") and isinstance(session["query_result"], dict):
                try:
                    session["query_result"] = QueryResult.model_validate(session["query_result"])
                except Exception as e:
                    logger.warning("query_result_restore_error", error=str(e))
                    session["query_result"] = None

            return session
        except Exception as e:
            logger.error("load_session_error", error=str(e))
        return None

    async def _save_session(self, key: str, state: dict[str, Any]) -> None:
        """Save session state to Redis."""
        try:
            save_state = {}
            allowed_keys = (
                "phone_number",
                "account_id",
                "account_ids",
                "current_account_index",
                "account_info",
                "current_page",
                "page_size",
                "total_results",
                "has_more",
                "cached_transactions",
                "language",
                "session_active",
                "query",
                "query_result",
            )
            for k, v in state.items():
                if k not in allowed_keys:
                    continue
                if k in ("query", "query_result") and v and hasattr(v, "model_dump"):
                    save_state[k] = v.model_dump()
                elif k == "cached_transactions" and v:
                    save_state[k] = [t.model_dump() if hasattr(t, "model_dump") else t for t in v]
                else:
                    save_state[k] = v
            await self.redis.set(key, json.dumps(save_state), ex=SESSION_TTL)
        except Exception as e:
            logger.error("save_session_error", error=str(e))

    async def _clear_session(self, key: str) -> None:
        """Clear session state from Redis."""
        try:
            await self.redis.delete(key)
        except Exception as e:
            logger.error("clear_session_error", error=str(e))
