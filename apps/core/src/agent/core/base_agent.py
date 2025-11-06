"""Base class for all sub-agents."""
from abc import ABC, abstractmethod
from typing import Any

from langchain_openai import ChatOpenAI


class BaseAgent(ABC):
    """Base class for all sub-agents with common functionality."""

    def __init__(self, llm: ChatOpenAI | None = None, model: str = "gpt-4o-mini", temperature: float = 0) -> None:
        """Initialize the agent with LLM and PostgreSQL checkpointer."""
        self.llm = llm or ChatOpenAI(model=model, temperature=temperature)

        # Initialize PostgreSQL checkpointer for async operations
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        # Store the context manager
        self._checkpointer_cm = AsyncPostgresSaver.from_conn_string(
            conn_string=self._get_database_url()
        )

        # Will be set when context manager is entered
        self.memory = None
        self._checkpointer_setup = False
        self._graph_compiled = False

        # Build graph structure (but don't compile yet - checkpointer not ready)
        self._graph_builder = self._build_graph()
        self.graph = None  # Will be compiled after checkpointer is ready

    async def _ensure_checkpointer(self):
        """Ensure checkpointer is initialized and graph is compiled."""
        if not self._checkpointer_setup:
            self.memory = await self._checkpointer_cm.__aenter__()
            self._checkpointer_setup = True

        # Compile graph now that checkpointer is ready
        if not self._graph_compiled:
            self.graph = self._graph_builder.compile(checkpointer=self.memory)
            self._graph_compiled = True

    def _get_database_url(self) -> str:
        """Get database URL for checkpoint storage."""
        import os
        db_url = os.getenv("DATABASE_URL", "")
        if not db_url:
            raise ValueError(
                "DATABASE_URL environment variable is required for checkpoint persistence")
        return db_url

    @abstractmethod
    def _build_graph(self) -> Any:
        """
        Build the LangGraph workflow (StateGraph).

        IMPORTANT: Subclasses should return the StateGraph BEFORE compiling it.
        The graph will be compiled automatically by _ensure_checkpointer().

        Returns:
            StateGraph (not compiled)
        """
        raise NotImplementedError("Subclasses must implement _build_graph")

    @abstractmethod
    async def invoke(self, phone_number: str, message: str, message_id: str) -> str:
        """
        Invoke the agent with user message.

        Subclasses MUST call await self._ensure_checkpointer() before using self.graph

        Args:
            phone_number: User's phone number
            message: User's message
            message_id: Unique message identifier

        Returns:
            Agent response string
        """
        raise NotImplementedError("Subclasses must implement invoke")

    def _get_config(self, phone_number: str, message_id: str | None = None) -> dict[str, Any]:
        """Get standard config for graph invocation.

        Uses agent-specific namespace + thread_id for checkpoint isolation.
        Each agent type gets its own checkpoint namespace to prevent overwrites.

        Args:
            phone_number: User's phone number
            message_id: Optional message ID (kept for compatibility, not used)
        """
        # Get agent-specific namespace (e.g., "TransferAgent", "QueryAgent")
        agent_namespace = self.__class__.__name__
        # Combine namespace with phone_number for unique thread_id per agent type
        thread_id = f"{agent_namespace}:{phone_number}"
        return {"configurable": {"thread_id": thread_id}}
