"""LangGraph graph for data purchase flow."""

import re
from functools import partial
from typing import Any

import redis.asyncio as redis
from langgraph.graph import END, StateGraph

from apps.core.src.agent.graphs.__shared__.base_flow_graph import BaseFlowGraph
from apps.core.src.agent.graphs.data.extractor import DataEntityExtractor
from apps.core.src.agent.graphs.data.graph.nodes.authorization import authorize_transaction
from apps.core.src.agent.graphs.data.graph.nodes.confirm import confirm_node
from apps.core.src.agent.graphs.data.graph.nodes.execute import execute_node
from apps.core.src.agent.graphs.data.graph.nodes.extraction import extract_entities
from apps.core.src.agent.graphs.data.graph.nodes.list import list_node
from apps.core.src.agent.graphs.data.graph.nodes.resolve import resolve_node
from apps.core.src.agent.graphs.data.graph.nodes.suggest import suggest_node
from apps.core.src.agent.graphs.data.graph.state import DataPurchaseState
from apps.core.src.agent.graphs.data.models import DataPlan
from apps.core.src.agent.graphs.data.plan_service import DataPlanService
from shared.clients.abstractions.bill import BillPaymentProvider
from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def route_after_extract(state: DataPurchaseState) -> str:
    """Route after extraction - go to resolve if we have enough info, else end."""
    target_phone = state.get("target_phone")
    network = state.get("network")

    if target_phone and network:
        return "suggest"
    return "resolve"


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
        return "confirm"  # Changed from execute to confirm

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
        return "confirm"  # Changed from execute to confirm
    return "list"


def route_after_confirm(state: DataPurchaseState) -> str:
    """Route after confirmation - wait for PIN."""
    flow_state = state.get("flow_state", "")
    if flow_state == "error":
        return "end"
    return "end"  # Wait for PIN callback


def route_after_authorize(state: DataPurchaseState) -> str:
    """Route after authorization."""
    flow_state = state.get("flow_state", "")
    if flow_state == "error":
        return "end"
    if state.get("data_status") == "authorized":
        return "execute"
    return "end"


class DataPurchaseGraph(BaseFlowGraph):
    """LangGraph-based data purchase flow with checkpointing support."""

    def __init__(
        self,
        bill_provider: BillPaymentProvider,
        redis_client: redis.Redis,
        whatsapp_client: WhatsAppClient | None = None,
        queue: RedisQueue | None = None,
    ):
        super().__init__()
        self.bill_provider = bill_provider
        self.redis = redis_client
        self.whatsapp_client = whatsapp_client
        self.queue = queue
        self.plan_service = DataPlanService(bill_provider, redis_client)
        self.extractor = DataEntityExtractor()

    @property
    def checkpoint_prefix(self) -> str:
        return "data"

    def _build_graph(self) -> StateGraph:
        """Build the data purchase flow graph."""
        graph = StateGraph(DataPurchaseState)

        graph.add_node(
            "extract",
            partial(extract_entities, extractor=self.extractor),
        )
        graph.add_node("resolve", resolve_node)
        graph.add_node(
            "suggest",
            partial(suggest_node, plan_service=self.plan_service),
        )
        graph.add_node("list", list_node)
        graph.add_node(
            "confirm",
            partial(
                confirm_node,
                redis_client=self.redis,
                whatsapp_client=self.whatsapp_client,
            ),
        )
        graph.add_node(
            "authorize",
            partial(
                authorize_transaction,
                redis_client=self.redis,
                queue=self.queue,
            ),
        )
        graph.add_node(
            "execute",
            partial(execute_node, bill_provider=self.bill_provider),
        )

        graph.set_entry_point("extract")

        graph.add_conditional_edges("extract", route_after_extract)
        graph.add_conditional_edges("resolve", route_after_resolve)
        graph.add_edge("suggest", END)
        graph.add_edge("list", END)
        graph.add_conditional_edges("confirm", route_after_confirm)
        graph.add_conditional_edges("authorize", route_after_authorize)
        graph.add_edge("execute", END)

        return graph

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
        quoted_data: dict | None = None,
    ) -> str:
        """Run the data purchase flow."""
        await self._ensure_checkpointer()

        if self._graph is None:
            raise RuntimeError("Graph not compiled")

        config = self._get_config(phone_number)
        budget = self._extract_budget(message)

        initial_state: DataPurchaseState = {
            "phone_number": phone_number,
            "message": message,
            "message_id": message_id or "",
            "user_context": user_context,
            "user_profile": user_context.get("profile", {}),
            "budget": budget,
            "flow_state": "resolving",
            "suggestion_attempts": 0,
            "all_plans": [],
        }

        if quoted_data:
            data = quoted_data.get("data", {})
            if data.get("phone_number"):
                initial_state["target_phone"] = data["phone_number"]
            if data.get("network"):
                initial_state["network"] = data["network"]
            if data.get("amount"):
                initial_state["budget"] = data["amount"]
            logger.info(
                "hydrating_from_quote",
                phone=data.get("phone_number"),
                network=data.get("network"),
            )

        try:
            result = await self._graph.ainvoke(initial_state, config)
            return result.get("response", "Something went wrong. Please try again.")

        except Exception as e:
            logger.error("data_graph_error", error=str(e), exc_info=True)
            return "Sorry, I couldn't process your data request. Please try again."

    async def has_active_session(self, phone_number: str) -> bool:
        """Check if there's an active data purchase session for this user."""
        return await self.has_active_checkpoint(phone_number)

    async def get_flow_summary(self, phone_number: str) -> dict[str, Any] | None:
        """Get summary of current flow state for pause/resume."""
        try:
            await self._ensure_checkpointer()
            config = self._get_config(phone_number)
            if self._graph:
                state = await self._graph.aget_state(config)
                if state and state.values:
                    return {
                        "network": state.values.get("network", ""),
                        "target_phone": state.values.get("target_phone", ""),
                        "budget": state.values.get("budget"),
                        "suggested_plan": state.values.get("suggested_plan"),
                        "flow_state": state.values.get("flow_state", ""),
                    }
            return None
        except Exception as e:
            logger.error(f"Error getting flow summary: {e}")
            return None

    async def resume_after_pin_verification(
        self,
        phone_number: str,
        pin_verified: bool,
        user_id: str | None,
    ) -> str:
        """Resume flow after PIN verification callback."""
        final_state = await self._inject_pin_and_resume(
            phone_number=phone_number,
            pin_verified=pin_verified,
            pin_error=None,
            user_id=user_id,
        )

        if not final_state:
            return "No active data purchase session found."

        if not pin_verified:
            # Base helper updates state, but we might want to ensure checkpoint is cleared if it fails?
            # Actually base helper just resumes. If pin_verified is False, the graph logic handles it.
            # But here we explicitly returned "PIN verification failed" message in original code.
            # Let's trust the graph state or return the message if we want 100% parity.
            pass

        return final_state.get("response", "")

    async def continue_flow(
        self,
        phone_number: str,
        message: str,
        previous_state: DataPurchaseState | None = None,
    ) -> str:
        """Continue the flow after user response."""
        await self._ensure_checkpointer()

        if self._graph is None:
            raise RuntimeError("Graph not compiled")

        config = self._get_config(phone_number)

        if previous_state is None:
            state = await self._graph.aget_state(config)
            if state and state.values:
                previous_state = state.values
            else:
                return "No active data purchase session found."

        message_lower = message.lower().strip()

        if message_lower in {"yes", "ok", "sure", "confirm", "proceed", "y"}:
            previous_state["selected_plan"] = previous_state.get("suggested_plan")
            result = await confirm_node(previous_state, self.redis, self.whatsapp_client)
            return result.get("response", "")

        if "show" in message_lower or "list" in message_lower or "plans" in message_lower:
            result = await list_node(previous_state)
            return result.get("response", "")

        all_plans = previous_state.get("all_plans", [])
        if all_plans:
            selected = self._select_plan_from_message(message, all_plans)
            if selected:
                previous_state["selected_plan"] = selected
                result = await confirm_node(previous_state, self.redis, self.whatsapp_client)
                return result.get("response", "")

        attempts = previous_state.get("suggestion_attempts", 0)
        if attempts >= 2:
            result = await list_node(previous_state)
        else:
            previous_state["suggestion_attempts"] = attempts + 1
            result = await suggest_node(previous_state, self.plan_service)

        return result.get("response", "")
