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
        self.bill_provider = bill_provider
        self.redis = redis_client
        self.whatsapp_client = whatsapp_client

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

    async def get_last_state(self, phone: str) -> dict[str, Any] | None:
        """Get the last workflow state (checkpoint)."""
        return await self.graph.get_checkpoint_state(phone)

    async def preflight(self, phone: str, text: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Prepare data purchase task by enriching parameters and checking readiness.
        """
        from apps.core.src.agent.graphs.data.graph.nodes.extraction import extract_entities
        from apps.core.src.agent.graphs.data.graph.nodes.resolve import resolve_node
        from apps.core.src.agent.graphs.data.graph.state import DataPurchaseState

        # 1. Initialize State
        state: DataPurchaseState = {
            "phone_number": phone,
            "message": text,
            "budget": params.get("amount") or params.get("budget"),
            "target_phone": params.get("target_phone") or params.get("recipient_phone") or params.get("phone"),
            "network": params.get("network"),
            "flow_state": "resolving",
            "user_context": {},
            "user_profile": {},
        }

        # 2. Extract Entities
        state = await extract_entities(state, extractor=self.graph.extractor)

        # 3. Resolve Target/Network
        result = await resolve_node(state)

        # Merge result into state manually as nodes return dicts
        for k, v in result.items():
            state[k] = v  # type: ignore

        if state.get("flow_state") == "error":
            return {
                "ready": False,
                "question": state.get("response"),
                "missing_fields": ["network" if "network" in (state.get("error") or "").lower() else "target_phone"],
            }

        # 4. Success - Return Enriched Params
        # Note: Data graph doesn't currently load source accounts in resolve_node,
        # but we can provide the enriched target phone and network.

        return {
            "ready": True,
            "enriched_params": {
                "amount": state.get("budget"),
                "recipient_phone": state.get("target_phone"),
                "network": state.get("network"),
            },
        }
