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
from apps.core.src.agent.sub_agents.query.models import QueryResult
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
        session_data = await self._load_session(session_key)
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
        session_data = await self._load_session(session_key)
        session_active = session_data.get("session_active", False) if session_data else False

        if session_active and session_data:
            # Check if there's a pending support issue to route
            pending_support_item = session_data.get("pending_support_item")
            if pending_support_item:
                await self._clear_session(session_key)
                return {
                    "route_to_support": True,
                    "transaction": pending_support_item,
                    "message": message,
                    "phone_number": phone_number,
                    "user_id": user_ctx.get("user_id", ""),
                }

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
                # Only re-execute if NOT expanding (expanded items are already in state)
                if not state.get("show_expanded"):
                    state.update(await execute_node(state, self.executor))
                state.update(await format_node(state, self.llm))
            elif state.get("continuation_type") in ("time_delta", "filter_delta"):
                # If in recipient/expanded context, filter locally instead of re-fetching
                if state.get("show_expanded") and state.get("continuation_type") == "filter_delta":
                    filter_result = await self._handle_local_filter(state)
                    state.update(filter_result)
                else:
                    state.update(await execute_node(state, self.executor))
                state.update(await format_node(state, self.llm))
            elif state.get("continuation_type") == "expand":
                # Show underlying transactions from analytics result
                state["show_expanded"] = True
                state.update(await format_node(state, self.llm))
            elif state.get("continuation_type") == "drill_down":
                # Resolve drill-down from previous result
                drill_result = await self._handle_drill_down(state)
                state.update(drill_result)
                state.update(await format_node(state, self.llm))
            elif state.get("continuation_type") == "recipient_drill_down":
                # Show transactions for a specific recipient from beneficiary summary
                recipient_result = await self._handle_recipient_drill_down(state)
                state.update(recipient_result)
                if not state.get("response"):
                    state.update(await format_node(state, self.llm))
            elif state.get("continuation_type") == "end_session":
                # Response already set by parse node, session ends here
                pass
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
            "page_size": 5,
            "total_results": 0,
            "has_more": False,
            "cached_transactions": [],
            "language": user_ctx.get("language", "English"),
            "response": "",
        }

    async def _handle_drill_down(self, state: QueryState) -> dict[str, Any]:
        """Handle drill-down using index and action from classifier."""
        from apps.core.src.agent.sub_agents.query.receipt import format_text_receipt

        query_result = state.get("query_result")
        drill_down_index = state.get("drill_down_index", 0)
        drill_down_action = state.get("drill_down_action", "view_details")

        if not query_result or not query_result.items:
            return {"response": "No items to drill down into."}

        index = max(0, min(drill_down_index, len(query_result.items) - 1))
        item = query_result.items[index]

        if drill_down_action == "get_receipt":
            receipt = format_text_receipt(item)
            return {
                "response": receipt,
                "session_active": True,
            }

        elif drill_down_action == "report_issue":
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

        focused_result = QueryResult(
            summary_text=f"Details for {item.description}",
            items=[item],
            has_more=False,
        )

        return {
            "query_result": focused_result,
            "has_more": False,
        }

    async def _handle_recipient_drill_down(self, state: QueryState) -> dict[str, Any]:
        """Handle drill-down into a specific recipient's transactions."""
        from datetime import datetime

        from apps.core.src.agent.sub_agents.query.models import QueryResult, QueryResultItem

        query_result = state.get("query_result")
        recipient_name = state.get("recipient_name", "").lower()

        if not query_result or not query_result.items:
            return {
                "response": "No recipient data available.",
                "session_active": False,
            }

        # Find matching recipient in the items
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

        # Get transactions from metadata
        transactions = matched_item.metadata.get("transactions", []) if matched_item.metadata else []

        if not transactions:
            return {
                "response": f"No transaction details available for {matched_item.description}.",
                "session_active": True,
            }

        # Build transaction items for display
        items = []
        for i, t in enumerate(transactions[:10]):  # Limit to 10
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
            "response": None,  # Will be formatted by format_node
            "session_active": True,
        }

    async def _handle_local_filter(self, state: QueryState) -> dict[str, Any]:
        """Apply filter to current expanded items without re-fetching."""
        from apps.core.src.agent.sub_agents.query.models import QueryResult

        query_result = state.get("query_result")
        filters = state.get("filters")  # From continuation classification

        if not query_result or not query_result.items:
            return {"response": "No items to filter.", "session_active": False}

        filtered_items = list(query_result.items)

        if filters:
            # Filter by account/bank name
            if filters.account_filter:
                bank_filter = filters.account_filter.lower()
                filtered_items = [
                    item
                    for item in filtered_items
                    if item.metadata and bank_filter in item.metadata.get("bank_name", "").lower()
                ]

            # Filter by transaction type
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

        # Update summary to reflect filter
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
                "accounts",
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
                "pending_support_item",
                "show_expanded",
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
            await self.redis.set(key, json.dumps(save_state, default=str), ex=SESSION_TTL)
        except Exception as e:
            logger.error("save_session_error", error=str(e))

    async def _clear_session(self, key: str) -> None:
        """Clear session state from Redis."""
        try:
            await self.redis.delete(key)
        except Exception as e:
            logger.error("clear_session_error", error=str(e))
