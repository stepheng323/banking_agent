"""LangGraph graph for data purchase flow."""

import re
from functools import partial
from typing import Any

import redis.asyncio as redis
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph import END, StateGraph

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
from apps.core.src.agent.graphs.data.service import DataPlanService
from shared.clients.abstractions.bill import BillPaymentProvider
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config.settings import settings
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


class DataPurchaseGraph:
    """LangGraph-based data purchase flow with checkpointing support."""

    def __init__(
        self,
        bill_provider: BillPaymentProvider,
        redis_client: redis.Redis,
        whatsapp_client: WhatsAppClient | None = None,
        queue: RedisQueue | None = None,
    ):
        self.bill_provider = bill_provider
        self.redis = redis_client
        self.whatsapp_client = whatsapp_client
        self.queue = queue
        self.plan_service = DataPlanService(bill_provider, redis_client)
        self.extractor = DataEntityExtractor()

        self._graph = None
        self._checkpointer = None
        self._checkpointer_setup = False

    def _get_config(self, phone_number: str) -> RunnableConfig:
        """Get LangGraph config for a user."""
        return {"configurable": {"thread_id": f"data:{phone_number}"}}

    async def clear_checkpoint(self, phone_number: str) -> None:
        """Clear data purchase flow checkpoint for a user."""
        try:
            await self._ensure_checkpointer()
            config = self._get_config(phone_number)
            if self._checkpointer:
                thread_id = config["configurable"]["thread_id"]
                await self._checkpointer.adelete_thread(thread_id)
                logger.info(f"Cleared data checkpoint for {phone_number}")
            else:
                logger.warning(f"Checkpointer not initialized for {phone_number}")
        except Exception as e:
            logger.error(f"Error clearing data checkpoint: {e}")

    async def _ensure_checkpointer(self) -> None:
        """Ensure checkpointer is initialized and graph is compiled."""
        if not self._checkpointer_setup:
            self._checkpointer = AsyncRedisSaver(redis_url=settings.redis_url)
            await self._checkpointer.asetup()
            self._checkpointer_setup = True

        if self._graph is None:
            self._graph = self._build_graph().compile(checkpointer=self._checkpointer)

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

        try:
            result = await self._graph.ainvoke(initial_state, config)
            return result.get("response", "Something went wrong. Please try again.")

        except Exception as e:
            logger.error("data_graph_error", error=str(e), exc_info=True)
            return "Sorry, I couldn't process your data request. Please try again."

    async def has_active_session(self, phone_number: str) -> bool:
        """Check if there's an active data purchase session for this user."""
        try:
            await self._ensure_checkpointer()
            config = self._get_config(phone_number)
            if self._graph:
                state = await self._graph.aget_state(config)
                if state and state.values:
                    flow_state = state.values.get("flow_state", "")
                    return flow_state not in ("", "completed", "error")
            return False
        except Exception as e:
            logger.error(f"Error checking active session: {e}")
            return False

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
        await self._ensure_checkpointer()

        if self._graph is None:
            raise RuntimeError("Graph not compiled")

        config = self._get_config(phone_number)
        state = await self._graph.aget_state(config)

        if not state or not state.values:
            return "No active data purchase session found."

        updated_state = {
            **state.values,
            "pin_verified": pin_verified,
            "user_id": user_id,
        }

        if not pin_verified:
            await self.clear_checkpoint(phone_number)
            return "PIN verification failed. Please try again."

        try:
            result = await self._graph.ainvoke(updated_state, config)
            return result.get("response", "")
        except Exception as e:
            logger.error("data_resume_error", error=str(e), exc_info=True)
            return "Failed to complete data purchase. Please try again."

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

        # Confirmation triggers PIN flow
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
