"""Data purchase service facade using LangGraph."""

from typing import Any
import redis.asyncio as redis

from apps.core.src.agent.graphs.data.graph import DataPurchaseGraph
from apps.core.src.agent.graphs.interfaces import IAgentService
from shared.clients.abstractions.bill import BillPaymentProvider
from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class DataService(IAgentService):
    """Data purchase service facade using LangGraph."""

    def __init__(
        self,
        bill_provider: BillPaymentProvider,
        redis_client: redis.Redis,
        whatsapp_client: WhatsAppClient | None = None,
        queue: RedisQueue | None = None,
    ):
        """Initialize data purchase service.
        
        Args:
            bill_provider: Bill payment provider for data purchases
            redis_client: Redis client for caching
            whatsapp_client: Optional WhatsApp client
            queue: Optional Redis queue
        """
        self.graph = DataPurchaseGraph(
            bill_provider=bill_provider,
            redis_client=redis_client,
            whatsapp_client=whatsapp_client,
            queue=queue,
        )

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

    async def resume_after_pin_verification(
        self, phone_number: str, pin_verified: bool, extra_param: Any = None
    ) -> str:
        """Resume data purchase flow after PIN verification."""
        return await self.graph.resume_after_pin_verification(phone_number, pin_verified, extra_param)
