from typing import Any, TYPE_CHECKING

from langchain_core.runnables import Runnable

from apps.core.src.agent.graphs.interfaces import IAgentService
from apps.core.src.agent.graphs.query.graph.graph import QueryFlowGraph
from shared.utils.logging import get_logger

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.support.service import SupportService

logger = get_logger(__name__)


class QueryService(IAgentService):
    """Query service facade."""

    def __init__(
        self,
        llm: Runnable,
        banking_provider: Any,
        redis_client: Any,
        support_service: "SupportService | None" = None,
    ):
        self.graph = QueryFlowGraph(
            llm=llm,
            banking_provider=banking_provider,
            redis_client=redis_client,
        )
        self.support_service = support_service

    async def run_simple(
        self,
        phone: str,
        text: str,
        classification_result: dict | None = None,
        image_data: str | None = None,
        quoted_data: dict | None = None,
        user_context: dict | None = None,
    ) -> str:
        """Run the query flow."""
        result = await self.graph.run(phone, text, user_context or {})
        
        # Handle support routing if needed
        if isinstance(result, dict) and result.get("route_to_support"):
            if self.support_service:
                user_id = result.get("user_id", "") or (classification_result or {}).get("user_id", "")
                
                # Use SupportService to handle the routed request
                support_response = await self.support_service.run_simple(
                    phone=phone,
                    text=result.get("message", text),
                    classification_result={"user_id": user_id},
                    quoted_data={"transaction": result.get("transaction")},
                )
                return support_response or "I'm having trouble connecting you to support."
                
            return "Support is temporarily unavailable. Please try again later."
            
        return result if isinstance(result, str) else "Query processed."

    async def has_active_session(self, phone_number: str) -> bool:
        """Check if user has an active query session."""
        return await self.graph.has_active_session(phone_number)

    async def clear_checkpoint(self, phone_number: str) -> None:
        """Clear query flow checkpoint."""
        await self.graph.clear_checkpoint(phone_number)
