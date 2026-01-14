"""Data purchase service facade using LangGraph."""

from apps.core.src.agent.graphs.data.graph import DataPurchaseGraph
from apps.core.src.agent.graphs.interfaces import ITransactionService
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class DataService(ITransactionService):
    """Data purchase service facade using LangGraph."""

    def __init__(self, graph: DataPurchaseGraph):
        self.graph = graph

    async def run_simple(
        self,
        phone: str,
        text: str,
        classification_result: dict | None = None,
        image_data: str | None = None,
        quoted_data: dict | None = None,
    ) -> str:
        """Run the data purchase flow using LangGraph."""
        logger.debug("data_flow_started", phone=phone, message=text[:100])
        return await self.graph.run(
            phone_number=phone,
            message=text,
            user_context={},
            quoted_data=quoted_data,
        )

    async def clear_checkpoint(self, phone_number: str) -> None:
        """Clear data flow checkpoint for a user."""
        try:
            await self.graph.clear_checkpoint(phone_number)
        except Exception as e:
            logger.error("data_checkpoint_clear_error", phone=phone_number, error=str(e), exc_info=True)
