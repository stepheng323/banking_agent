"""Query service facade."""

from apps.core.src.agent.graphs.interfaces import ITransactionService
from apps.core.src.agent.graphs.query.parser import QueryParser
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class QueryService(ITransactionService):
    """Query service facade."""

    def __init__(self, parser: QueryParser):
        self.parser = parser

    async def run_simple(
        self,
        phone: str,
        text: str,
        classification_result: dict | None = None,
        image_data: str | None = None,
        quoted_data: dict | None = None,
    ) -> str:
        """Run the query parser."""
        result = await self.parser.parse(text)
        return result.formatted_response if result else ""

    async def clear_checkpoint(self, phone_number: str) -> None:
        """No checkpoint for stateless query parser."""
        pass
