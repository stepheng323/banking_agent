"""Base class for all sub-agents."""
from abc import ABC, abstractmethod
from typing import Any

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver


class BaseAgent(ABC):
    """Base class for all sub-agents with common functionality."""

    def __init__(self, llm: ChatOpenAI | None = None, model: str = "gpt-4o-mini", temperature: float = 0) -> None:
        """Initialize the agent with LLM."""
        self.llm = llm or ChatOpenAI(model=model, temperature=temperature)
        self.memory = MemorySaver()
        self.graph = self._build_graph()

    @abstractmethod
    def _build_graph(self) -> Any:
        """Build the LangGraph workflow. Must be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement _build_graph")

    @abstractmethod
    async def invoke(self, phone_number: str, message: str, message_id: str) -> str:
        """
        Invoke the agent with user message.

        Args:
            phone_number: User's phone number
            message: User's message
            message_id: Unique message identifier

        Returns:
            Agent response string
        """
        raise NotImplementedError("Subclasses must implement invoke")

    def _get_config(self, phone_number: str, message_id: str) -> dict[str, Any]:
        """Get standard config for graph invocation."""
        return {"configurable": {"thread_id": phone_number, "thread_ts": message_id}}
