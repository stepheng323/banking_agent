"""Query service facade using QueryWorker."""

from typing import Any

import redis.asyncio as redis
from langchain_core.runnables import Runnable

from apps.core.src.agent.graphs.interfaces import IAgentService
from apps.core.src.agent.graphs.query.session import QuerySessionManager
from apps.core.src.agent.graphs.query.worker import QueryWorker
from apps.core.src.agent.graphs.support import SupportService
from shared.cache.user_data import UserDataCache
from shared.clients.abstractions.banking import BankingDataProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class QueryService(IAgentService):
    """Query service facade using QueryWorker."""

    def __init__(
        self,
        llm: Runnable,
        banking_provider: BankingDataProvider,
        redis_client: redis.Redis,
        user_cache: UserDataCache,
        support_service: SupportService | None = None,
    ):
        self.session_manager = QuerySessionManager(redis_client)
        self.worker = QueryWorker(llm, banking_provider, self.session_manager)
        self.user_cache = user_cache
        # support_service is kept for API compatibility but currently handled via logic in the graph/handler

    async def run_simple(
        self,
        phone: str,
        text: str,
        classification_result: dict | None = None,
        image_data: str | None = None,
        quoted_data: dict | None = None,
    ) -> str:
        """Run the query flow."""
        logger.debug("query_flow_started", phone=phone)

        # Fetch user context from cache
        cache_data = await self.user_cache.get_all_user_data(phone)
        user_ctx = {
            "accounts": cache_data.get("accounts") or [],
            "user_id": (cache_data.get("profile") or {}).get("id", ""),
            "profile": cache_data.get("profile"),
            "phone_number": phone, # Ensure phone is in context
            "params": classification_result or {} # Pass extra params if any
        }
        
        payload = {
            "message": text,
            "account_id": None, # Will be resolved from user_ctx accounts
        }

        result = await self.worker.run(payload, user_ctx)
        
        if result.outcome.is_successful:
            return result.response or "Query completed."
        
        if result.error:
            return f"⚠️ {result.error}"
            
        return "I couldn't process your request."

    async def preflight(
        self,
        phone: str,
        text: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Query doesn't require preflight validation."""
        return {
            "ready": True,
            "missing_fields": [],
            "enriched_params": params or {},
            "question": None,
        }

    async def clear_checkpoint(self, phone_number: str) -> None:
        """Clear flow checkpoint for a user."""
        try:
            # Query graph manages session manually via Redis, so we call clear on session manager
            await self.session_manager.clear(f"query:session:{phone_number}")
        except Exception as e:
            logger.error("query_checkpoint_clear_error", phone=phone_number, error=str(e), exc_info=True)
