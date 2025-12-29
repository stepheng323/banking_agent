"""LangGraph graph for data purchase flow."""

import re
from functools import partial
from typing import Any

import redis.asyncio as redis
from langgraph.graph import END, StateGraph

from apps.core.src.agent.sub_agents.data.graph.nodes.execute import execute_node
from apps.core.src.agent.sub_agents.data.graph.nodes.list import list_node
from apps.core.src.agent.sub_agents.data.graph.nodes.resolve import resolve_node
from apps.core.src.agent.sub_agents.data.graph.nodes.suggest import suggest_node
from apps.core.src.agent.sub_agents.data.graph.state import DataPurchaseState
from apps.core.src.agent.sub_agents.data.models import DataPlan
from apps.core.src.agent.sub_agents.data.service import DataPlanService
from shared.clients.abstractions.bill import BillPaymentProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def route_after_resolve(state: DataPurchaseState) -> str:
    """Route after resolving target and network."""
    flow_state = state.get("flow_state", "")
    if flow_state == "error":
        return "end"
    return "suggest"


def route_after_suggest(state: DataPurchaseState) -> str:
    """Route after suggestion - wait for user response."""
    return "end"


def route_user_response(state: DataPurchaseState) -> str:
    """Route based on user's response to suggestion."""
    message = state.get("message", "").lower().strip()
    attempts = state.get("suggestion_attempts", 0)

    if message in {"yes", "ok", "sure", "confirm", "proceed", "y"}:
        return "execute"

    if "show" in message or "list" in message or "plans" in message:
        return "list"

    if attempts >= 2:
        return "list"

    return "suggest"


def route_after_list(state: DataPurchaseState) -> str:
    """Route after showing list."""
    return "end"


def route_plan_selection(state: DataPurchaseState) -> str:
    """Route when user selects a plan from list."""
    selected = state.get("selected_plan")
    if selected:
        return "execute"
    return "list"


class DataPurchaseGraph:
    """LangGraph-based data purchase flow."""

    def __init__(
        self,
        bill_provider: BillPaymentProvider,
        redis_client: redis.Redis,
    ):
        self.bill_provider = bill_provider
        self.redis = redis_client
        self.plan_service = DataPlanService(bill_provider, redis_client)
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """Build the data purchase flow graph."""
        graph = StateGraph(DataPurchaseState)

        graph.add_node("resolve", resolve_node)
        graph.add_node(
            "suggest",
            partial(suggest_node, plan_service=self.plan_service),
        )
        graph.add_node("list", list_node)
        graph.add_node(
            "execute",
            partial(execute_node, bill_provider=self.bill_provider),
        )

        graph.set_entry_point("resolve")

        graph.add_conditional_edges("resolve", route_after_resolve)
        graph.add_edge("suggest", END)
        graph.add_edge("list", END)
        graph.add_edge("execute", END)

        return graph.compile()

    def _extract_budget(self, message: str) -> int | None:
        """Extract budget from message like 'data 2k' or 'buy data with 2000'."""
        match = re.search(r"(\d+)k\b", message.lower())
        if match:
            return int(match.group(1)) * 1000

        match = re.search(r"₦?([\d,]+)", message)
        if match:
            return int(match.group(1).replace(",", ""))

        return None

    def _select_plan_from_message(self, message: str, plans: list[DataPlan]) -> DataPlan | None:
        """Try to match user's selection to a plan."""
        message_lower = message.lower().strip()

        gb_match = re.search(r"(\d+(?:\.\d+)?)\s*gb", message_lower)
        mb_match = re.search(r"(\d+)\s*mb", message_lower)

        target_size = None
        if gb_match:
            target_size = float(gb_match.group(1))
        elif mb_match:
            target_size = float(mb_match.group(1)) / 1000

        if target_size:
            for plan in plans:
                if plan.size_gb and abs(plan.size_gb - target_size) < 0.1:
                    return plan

        return None

    async def run(
        self,
        phone_number: str,
        message: str,
        user_context: dict[str, Any],
        message_id: str | None = None,
    ) -> str:
        """
        Run the data purchase flow.

        Args:
            phone_number: User's phone number
            message: User's message
            user_context: User context with profile, accounts, etc.
            message_id: Optional message ID

        Returns:
            Response string to send to user
        """
        # Extract budget if present
        budget = self._extract_budget(message)

        initial_state: DataPurchaseState = {
            "phone_number": phone_number,
            "message": message,
            "message_id": message_id or "",
            "user_context": user_context,
            "budget": budget,
            "flow_state": "resolving",
            "suggestion_attempts": 0,
            "all_plans": [],
        }

        try:
            result = await self.graph.ainvoke(initial_state)
            return result.get("response", "Something went wrong. Please try again.")

        except Exception as e:
            logger.error("data_graph_error", error=str(e), exc_info=True)
            return "Sorry, I couldn't process your data request. Please try again."

    async def continue_flow(
        self,
        phone_number: str,
        message: str,
        previous_state: DataPurchaseState,
    ) -> str:
        """
        Continue the flow after user response.

        Args:
            phone_number: User's phone number
            message: User's response
            previous_state: State from previous graph run

        Returns:
            Response string
        """
        message_lower = message.lower().strip()

        if message_lower in {"yes", "ok", "sure", "confirm", "proceed", "y"}:
            previous_state["selected_plan"] = previous_state.get("suggested_plan")
            result = await execute_node(previous_state, self.bill_provider)
            return result.get("response", "")

        if "show" in message_lower or "list" in message_lower or "plans" in message_lower:
            result = await list_node(previous_state)
            return result.get("response", "")
        all_plans = previous_state.get("all_plans", [])
        if all_plans:
            selected = self._select_plan_from_message(message, all_plans)
            if selected:
                previous_state["selected_plan"] = selected
                result = await execute_node(previous_state, self.bill_provider)
                return result.get("response", "")

        attempts = previous_state.get("suggestion_attempts", 0)
        if attempts >= 2:
            result = await list_node(previous_state)
        else:
            previous_state["suggestion_attempts"] = attempts + 1
            result = await suggest_node(previous_state, self.plan_service)

        return result.get("response", "")
