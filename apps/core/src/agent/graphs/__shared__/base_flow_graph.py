"""Base class for transactional flow graphs.

Provides shared checkpointing, configuration, and lifecycle management
for Transfer, Airtime, and Data purchase flows.
"""

from abc import ABC, abstractmethod
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph.state import CompiledStateGraph

from shared.config.settings import settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class BaseFlowGraph(ABC):
    """
    Abstract base class for all transactional flow graphs.

    Provides common functionality:
    - Redis-based checkpointing for flow state persistence
    - Standard configuration generation
    - Checkpoint lifecycle management

    Subclasses must implement:
    - checkpoint_prefix: Unique prefix for this flow type (e.g., "transfer", "airtime")
    - _build_graph(): Build and return the compiled LangGraph
    """

    def __init__(self) -> None:
        self._checkpointer: AsyncRedisSaver | None = None
        self._checkpointer_setup: bool = False
        self._graph: CompiledStateGraph | None = None

    @property
    @abstractmethod
    def checkpoint_prefix(self) -> str:
        """
        Unique prefix for checkpoint thread IDs.

        Examples: "transfer", "airtime", "data"
        """
        ...

    @abstractmethod
    def _build_graph(self) -> CompiledStateGraph:
        """
        Build and compile the LangGraph for this flow.

        Returns:
            Compiled StateGraph with checkpointer configured
        """
        ...

    def _get_config(self, phone_number: str) -> RunnableConfig:
        """Get LangGraph config for a user session."""
        return {"configurable": {"thread_id": f"{self.checkpoint_prefix}:{phone_number}"}}

    async def _ensure_checkpointer(self) -> None:
        """Ensure checkpointer is initialized and graph is compiled."""
        if not self._checkpointer_setup:
            self._checkpointer = AsyncRedisSaver(redis_url=settings.redis_url)
            await self._checkpointer.asetup()
            self._checkpointer_setup = True

        if self._graph is None:
            self._graph = self._build_graph()

    async def clear_checkpoint(self, phone_number: str) -> None:
        """Clear flow checkpoint for a user."""
        try:
            await self._ensure_checkpointer()
            config = self._get_config(phone_number)
            if self._checkpointer:
                thread_id = config["configurable"]["thread_id"]
                await self._checkpointer.adelete_thread(thread_id)
                logger.info(
                    "checkpoint_cleared",
                    flow=self.checkpoint_prefix,
                    phone=phone_number[:6] if phone_number else None,
                )
            else:
                logger.warning(
                    "checkpointer_not_initialized",
                    flow=self.checkpoint_prefix,
                    phone=phone_number[:6] if phone_number else None,
                )
        except Exception as e:
            logger.error(
                "checkpoint_clear_error",
                flow=self.checkpoint_prefix,
                error=str(e),
            )

    async def get_checkpoint_state(self, phone_number: str) -> dict[str, Any] | None:
        """
        Get the current checkpoint state for a user session.

        Returns:
            State dict if checkpoint exists, None otherwise
        """
        try:
            await self._ensure_checkpointer()
            if not self._graph:
                return None

            config = self._get_config(phone_number)
            state = await self._graph.aget_state(config)

            if state and state.values:
                return dict(state.values)
            return None
        except Exception as e:
            logger.warning(
                "checkpoint_state_fetch_error",
                flow=self.checkpoint_prefix,
                error=str(e),
            )
            return None

    async def has_active_checkpoint(self, phone_number: str) -> bool:
        """Check if there's an active checkpoint for this user."""
        state = await self.get_checkpoint_state(phone_number)
        return state is not None

    async def _inject_pin_and_resume(
        self,
        phone_number: str,
        pin_verified: bool,
        pin_error: str | None,
        redis_client: Any,  # Should be RedisClient type, but avoiding circ import
    ) -> dict[str, Any]:
        """
        Inject PIN verification result into state and resume graph execution.

        Common pattern for all flows after confirm sends PIN prompt.
        1. Gets current message ID
        2. Updates state with PIN result
        3. Resumes graph
        """
        await self._ensure_checkpointer()
        if not self._graph:
            raise RuntimeError("Graph not compiled")

        config = self._get_config(phone_number)
        current_state = await self._graph.aget_state(config)

        if not current_state or not current_state.values:
            return {}

        current_message_id = await redis_client.get(f"user:{phone_number}:current_message_id")
        state_message_id = current_state.values.get("message_id")

        await self._graph.aupdate_state(
            config,
            {
                "pin_verified": pin_verified,
                "pin_verification_error": pin_error,
                "message_id": current_message_id or state_message_id,
            },
        )

        logger.info(
            "pin_resume_starting",
            flow=self.checkpoint_prefix,
            phone=phone_number[:6] if phone_number else None,
            pin_verified=pin_verified,
        )

        final_state = await self._graph.ainvoke(None, config)
        return final_state

    async def _get_conversation_state(self, phone_number: str, redis_client: Any) -> dict | None:
        """Fetch and parse conversation_state from Redis."""
        try:
            state_json = await redis_client.get(f"user:{phone_number}:conversation_state")
            if not state_json:
                return None
            import json

            return json.loads(state_json)
        except Exception as e:
            logger.warning("conversation_state_fetch_error", error=str(e))
            return None
