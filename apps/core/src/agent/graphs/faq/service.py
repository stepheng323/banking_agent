"""FAQ service facade using LangGraph."""

from collections.abc import Callable

from langchain_core.runnables import Runnable
from sqlalchemy.orm import Session

from apps.core.src.agent.graphs.faq.graph.graph import FAQFlowGraph
from apps.core.src.agent.graphs.interfaces import IAgentService
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class FAQService(IAgentService):
    """FAQ service facade using LangGraph."""

    def __init__(self, llm: Runnable, get_db: Callable[[], Session]):
        self.graph = FAQFlowGraph(llm=llm, get_db=get_db)

    async def run_simple(
        self,
        phone: str,
        text: str,
        classification_result: dict | None = None,
        image_data: str | None = None,
        quoted_data: dict | None = None,
    ) -> str:
        """Run the FAQ flow."""
        result = await self.graph.run(phone_number=phone, message=text)
        return result.get("response", "")

    async def clear_checkpoint(self, phone_number: str) -> None:
        """No checkpoint for FAQ flow."""
        pass
