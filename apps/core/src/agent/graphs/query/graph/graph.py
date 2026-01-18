"""LangGraph graph for query flow."""

from datetime import date
from functools import partial
from typing import Any

import redis.asyncio as redis
from langchain_core.runnables import Runnable
from langgraph.graph import END, StateGraph

from apps.core.src.agent.graphs.__shared__.account_selection.mandate_validator import validate_mandate_status
from apps.core.src.agent.graphs.query.continuity import ContinuationClassifier
from apps.core.src.agent.graphs.query.executor import QueryExecutor
from apps.core.src.agent.graphs.query.graph.handlers import (
    handle_drill_down,
    handle_local_filter,
    handle_recipient_drill_down,
    handle_unclear,
    record_query_success,
)
from apps.core.src.agent.graphs.query.graph.nodes.execute import execute_node
from apps.core.src.agent.graphs.query.graph.nodes.format import format_node
from apps.core.src.agent.graphs.query.graph.nodes.parse import (
    classify_continuation_node,
    paginate_node,
    parse_node,
)
from apps.core.src.agent.graphs.query.graph.routes import route_after_execute, route_after_parse
from apps.core.src.agent.graphs.query.graph.session import QuerySessionManager
from apps.core.src.agent.graphs.query.graph.state import QueryState
from apps.core.src.agent.graphs.query.parser import QueryParser
from shared.clients.abstractions.banking import BankingDataProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)


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
        self.session_manager = QuerySessionManager(redis_client)
        self.parser = QueryParser(llm)
        self.executor = QueryExecutor(banking_provider)
        self.continuation_classifier = ContinuationClassifier(llm)
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """Build the query flow graph."""
        graph = StateGraph(QueryState)

        graph.add_node("parse", partial(parse_node, parser=self.parser))
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

    async def has_active_session(self, phone_number: str) -> bool:
        """Check if there's an active query session for this user."""
        session_key = f"query:session:{phone_number}"
        session_data = await self.session_manager.load(session_key)
        return session_data.get("session_active", False) if session_data else False

    async def run(
        self,
        phone_number: str,
        message: str,
        user_ctx: dict[str, Any],
        message_id: str = "",
    ) -> str | dict[str, Any]:
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
        session_data = await self.session_manager.load(session_key)
        session_active = session_data.get("session_active", False) if session_data else False

        if session_active and session_data:
            result = await self._handle_continuation(
                session_data, message, message_id, phone_number, account_id, accounts, user_ctx
            )
            if isinstance(result, dict) and result.get("route_to_support"):
                await self.session_manager.clear(session_key)
                return result
        else:
            state = self._create_initial_state(phone_number, message, message_id, account_id, accounts, user_ctx)
            result = await self.graph.ainvoke(state)

        if result.get("response") and result.get("query_result"):
            result = record_query_success(result)

        if result.get("session_active"):
            await self.session_manager.save(session_key, result)
        else:
            await self.session_manager.clear(session_key)

        return result.get("response", "Query completed.")

    async def _handle_continuation(
        self,
        session_data: dict[str, Any],
        message: str,
        message_id: str,
        phone_number: str,
        account_id: str,
        accounts: list,
        user_ctx: dict,
    ) -> dict[str, Any]:
        """Handle continuation of an active session."""
        pending_support_item = session_data.get("pending_support_item")
        if pending_support_item:
            return {
                "route_to_support": True,
                "transaction": pending_support_item,
                "message": message,
                "phone_number": phone_number,
                "user_id": user_ctx.get("user_id", ""),
            }

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

        cont_type = state.get("continuation_type")

        if cont_type == "show_more":
            state.update(await paginate_node(state))
            if not state.get("show_expanded"):
                state.update(await execute_node(state, self.executor))
            state.update(await format_node(state, self.llm))

        elif cont_type in ("time_delta", "filter_delta"):
            limitation = await self._check_continuation_capabilities(state)
            if limitation:
                state["response"] = limitation
                state["session_active"] = True
                return state

            if state.get("show_expanded") and cont_type == "filter_delta":
                state.update(handle_local_filter(state))
            else:
                state.update(await execute_node(state, self.executor))
            state.update(await format_node(state, self.llm))

        elif cont_type == "expand":
            state["show_expanded"] = True
            state.update(await format_node(state, self.llm))

        elif cont_type == "drill_down":
            state.update(await handle_drill_down(state))
            if not state.get("response"):
                state.update(await format_node(state, self.llm))

        elif cont_type == "recipient_drill_down":
            state.update(handle_recipient_drill_down(state))
            if not state.get("response"):
                state.update(await format_node(state, self.llm))

        elif cont_type == "unclear":
            state.update(handle_unclear(state))

        elif cont_type == "end_session":
            pass

        else:
            state = await self._run_new_query(state, phone_number, message, message_id, account_id, accounts, user_ctx)

        return state

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
            "page_size": 5,
            "total_results": 0,
            "has_more": False,
            "cached_transactions": [],
            "language": user_ctx.get("language", "English"),
            "response": "",
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

    async def _check_continuation_capabilities(self, state: QueryState) -> str | None:
        """Check if updated query exceeds capabilities. Returns limitation message or None."""
        from apps.core.src.agent.graphs.query.capabilities import (
            check_capabilities,
            derive_requirements,
            generate_limitation_message,
        )

        query = state.get("query")
        if not query:
            return None

        requires = derive_requirements(query)
        missing = check_capabilities(requires)

        if missing:
            logger.info(
                "query_midflow_capability_limitation",
                missing=[cap.value for cap in missing],
            )
            return generate_limitation_message(missing)
        return None
